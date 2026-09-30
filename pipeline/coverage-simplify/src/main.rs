use anyhow::{Context, Result, ensure};
use clap::Parser;
use coverage_simplify::{
    edges::Coverage,
    io,
    native::{GeosApi, ProjApi},
    tpvw, validate,
};
use rayon::prelude::*;
use std::{
    fs,
    path::{Path, PathBuf},
    time::Instant,
};

#[derive(Parser)]
#[command(about = "Parallel GEOS 3.13.1 coverage-simplify port")]
struct Args {
    #[arg(long)]
    year: u32,
    #[arg(long)]
    in_root: PathBuf,
    #[arg(long)]
    out_root: PathBuf,
    #[arg(long,num_args=1..)]
    tiles: Vec<String>,
    #[arg(long)]
    tile_list: Option<PathBuf>,
    #[arg(long, default_value_t = 5.0, alias = "tolerance")]
    tolerance_m: f64,
    #[arg(long, default_value_t = 1)]
    threads: usize,
    #[arg(long, default_value_t = 0)]
    shard: usize,
    #[arg(long, default_value_t = 1)]
    num_shards: usize,
    #[arg(long)]
    geos_lib: Option<PathBuf>,
    #[arg(long)]
    proj_lib: Option<PathBuf>,
    #[arg(long)]
    proj_data: Option<PathBuf>,
    /// Compare the Rust coverage-invalid mask against GEOS; fail on differences.
    #[arg(long)]
    audit_validator: bool,
}
fn native_paths(a: &Args) -> Result<(PathBuf, PathBuf, PathBuf)> {
    if let (Some(g), Some(p), Some(d)) = (&a.geos_lib, &a.proj_lib, &a.proj_data) {
        return Ok((g.clone(), p.clone(), d.clone()));
    }
    anyhow::bail!("supply --geos-lib, --proj-lib and --proj-data together")
}
struct Timer {
    start: Instant,
    last: Instant,
    tile: String,
}
impl Timer {
    fn mark(&mut self, stage: &str, detail: serde_json::Value) {
        let now = Instant::now();
        let status = fs::read_to_string("/proc/self/status").unwrap_or_default();
        let mem = |key: &str| {
            status
                .lines()
                .find(|l| l.starts_with(key))
                .and_then(|l| l.split_whitespace().nth(1))
                .and_then(|s| s.parse::<u64>().ok())
                .unwrap_or(0)
        };
        println!(
            "{}",
            serde_json::json!({"tile":self.tile,"stage":stage,"seconds":(now-self.last).as_secs_f64(),"elapsed":(now-self.start).as_secs_f64(),"rss_kib":mem("VmRSS:"),"peak_kib":mem("VmHWM:"),"detail":detail})
        );
        self.last = now;
    }
}
fn process(a: &Args, tile: &str, proj: &ProjApi, geos: &GeosApi, data: &Path) -> Result<()> {
    ensure!(
        tile.len() >= 5 && tile.bytes().all(|c| c.is_ascii_alphanumeric() || c == b'_'),
        "invalid tile key"
    );
    let zone: u32 = tile[..2].parse()?;
    ensure!((1..=60).contains(&zone), "invalid UTM zone");
    let epsg = if tile.as_bytes()[2] >= b'N' {
        32600 + zone
    } else {
        32700 + zone
    };
    let src = a
        .in_root
        .join(a.year.to_string())
        .join(format!("{tile}.parquet"));
    let dst = a
        .out_root
        .join(a.year.to_string())
        .join(format!("{tile}.parquet"));
    let fp = io::fingerprint(&src, a.tolerance_m)?;
    if io::current(&dst, &fp) {
        println!("{}", serde_json::json!({"tile":tile,"skipped":true}));
        return Ok(());
    }
    let start = Instant::now();
    let mut timer = Timer {
        start,
        last: start,
        tile: tile.into(),
    };
    let mut input = io::read(&src)?;
    timer.mark("read_decode",serde_json::json!({"parcels":input.geoms.len(),"vertices":input.geoms.iter().map(|g|g.points().count()).sum::<usize>()}));
    input
        .geoms
        .par_chunks_mut(512)
        .try_for_each(|chunk| -> Result<()> {
            let tr = proj.transformer(epsg, data)?;
            for g in chunk {
                tr.apply(g, false)?;
            }
            Ok(())
        })?;
    timer.mark("project", serde_json::json!({}));
    if a.tolerance_m > 0. && !input.geoms.is_empty() {
        let bad = validate::invalid_mask(&input.geoms);
        timer.mark(
            "coverage_validate",
            serde_json::json!({"bad":bad.iter().filter(|&&b|b).count()}),
        );
        if a.audit_validator {
            let oracle = geos.context()?.invalid_mask(&input.geoms)?;
            let diff: Vec<_> = bad
                .iter()
                .zip(&oracle)
                .enumerate()
                .filter(|(_, (a, b))| a != b)
                .map(|(i, _)| i)
                .collect();
            ensure!(
                diff.is_empty(),
                "validator differs from GEOS at {} parcels: {:?}",
                diff.len(),
                &diff[..diff.len().min(20)]
            );
            timer.mark("validator_audit", serde_json::json!({"differences":0}));
        }
        let coverage = Coverage::extract(&input.geoms, &bad);
        timer.mark("extract", serde_json::json!({"edges":coverage.edges.len()}));
        let (out, rounds) = tpvw::simplify(&coverage.edges, a.tolerance_m);
        timer.mark("tpvw", serde_json::json!({"rounds":rounds}));
        coverage.rebuild(&mut input.geoms, &bad, &out);
        drop(coverage);
        drop(out);
        let repaired: usize = input
            .geoms
            .par_chunks_mut(512)
            .zip(bad.par_chunks(512))
            .map(|(chunk, bad)| -> Result<usize> {
                let ctx = geos.context()?;
                let mut n = 0;
                for (g, &b) in chunk.iter_mut().zip(bad) {
                    n += usize::from(ctx.finish(g, b, a.tolerance_m)?);
                }
                Ok(n)
            })
            .collect::<Result<Vec<_>>>()?
            .into_iter()
            .sum();
        timer.mark("rebuild_repair",serde_json::json!({"repaired":repaired,"vertices":input.geoms.iter().map(|g|g.points().count()).sum::<usize>()}));
    }
    input
        .geoms
        .par_chunks_mut(512)
        .try_for_each(|chunk| -> Result<()> {
            let tr = proj.transformer(epsg, data)?;
            for g in chunk {
                tr.apply(g, true)?;
            }
            Ok(())
        })?;
    timer.mark("inverse", serde_json::json!({}));
    ensure!(
        io::fingerprint(&src, a.tolerance_m)? == fp,
        "input changed while processing"
    );
    io::write(input, &dst, fp)?;
    timer.mark("write", serde_json::json!({}));
    Ok(())
}
fn main() -> Result<()> {
    let a = Args::parse();
    ensure!(
        a.threads > 0 && a.num_shards > 0 && a.shard < a.num_shards,
        "invalid threads/shard"
    );
    ensure!(a.tolerance_m.is_finite(), "nonfinite tolerance");
    fs::create_dir_all(&a.out_root)?;
    let out = a.out_root.canonicalize()?;
    ensure!(
        out != a.in_root.canonicalize()?,
        "input and output roots must differ"
    );
    let (g, p, data) = native_paths(&a)?;
    let geos = GeosApi::load(&g)?;
    let proj = ProjApi::load(&p)?;
    let mut tiles = a.tiles.clone();
    if tiles.is_empty() {
        if let Some(path) = &a.tile_list {
            tiles = fs::read_to_string(path)?
                .split_whitespace()
                .map(str::to_owned)
                .collect();
        } else {
            tiles = fs::read_dir(a.in_root.join(a.year.to_string()))?
                .filter_map(|v| v.ok())
                .map(|v| v.path())
                .filter(|p| p.extension().is_some_and(|x| x == "parquet"))
                .map(|p| p.file_stem().unwrap().to_string_lossy().into_owned())
                .collect();
            tiles.sort();
        }
    }
    let pool = rayon::ThreadPoolBuilder::new()
        .num_threads(a.threads)
        .build()?;
    pool.install(|| {
        for tile in tiles.iter().skip(a.shard).step_by(a.num_shards) {
            process(&a, tile, &proj, &geos, &data).with_context(|| format!("tile {tile}"))?;
        }
        Ok(())
    })
}
