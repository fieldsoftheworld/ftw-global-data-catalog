//! Requires NATIVE_SITE_PACKAGES and wheel libraries on LD_LIBRARY_PATH.
use coverage_simplify::{
    geom::{Geometry, Point},
    native::{GeosApi, ProjApi},
    wkb,
};
use std::path::{Path, PathBuf};
fn find(dir: &Path, prefix: &str) -> PathBuf {
    std::fs::read_dir(dir)
        .unwrap()
        .map(|e| e.unwrap().path())
        .find(|p| p.file_name().unwrap().to_string_lossy().starts_with(prefix))
        .unwrap()
}
fn site() -> PathBuf {
    PathBuf::from(std::env::var("NATIVE_SITE_PACKAGES").expect("set NATIVE_SITE_PACKAGES"))
}
fn unhex(s: &str) -> Vec<u8> {
    s.as_bytes()
        .as_chunks::<2>()
        .0
        .iter()
        .map(|v| u8::from_str_radix(std::str::from_utf8(v).unwrap(), 16).unwrap())
        .collect()
}
#[test]
#[ignore = "requires PROJ wheel libraries and NATIVE_SITE_PACKAGES"]
fn projection_matches_pyproj_under_one_millimetre() {
    let site = site();
    let api = ProjApi::load(&find(&site.join("pyproj.libs"), "libproj-")).unwrap();
    let cases: serde_json::Value = serde_json::from_str(include_str!("projection.json")).unwrap();
    let mut max: f64 = 0.;
    for c in cases.as_array().unwrap() {
        let tr = api
            .transformer(
                c["epsg"].as_u64().unwrap() as u32,
                &site.join("pyproj/proj_dir/share/proj"),
            )
            .unwrap();
        let p = Point {
            x: c["lon"].as_f64().unwrap(),
            y: c["lat"].as_f64().unwrap(),
        };
        let mut g = Geometry {
            polygons: vec![vec![vec![p]]],
            ..Geometry::default()
        };
        tr.apply(&mut g, false).unwrap();
        let q = *g.points().next().unwrap();
        max = max.max((q.x - c["x"].as_f64().unwrap()).hypot(q.y - c["y"].as_f64().unwrap()));
        tr.apply(&mut g, true).unwrap();
        let q = *g.points().next().unwrap();
        assert!((q.x - c["back_lon"].as_f64().unwrap()).abs() < 1e-12);
        assert!((q.y - c["back_lat"].as_f64().unwrap()).abs() < 1e-12);
    }
    eprintln!("max projection error vs pyproj: {max} m");
    assert!(max < 1e-6);
}
#[test]
#[ignore = "requires GEOS wheel libraries and NATIVE_SITE_PACKAGES"]
fn fallback_and_make_valid_match_python() {
    let api = GeosApi::load(&find(&site().join("shapely.libs"), "libgeos_c-")).unwrap();
    let ctx = api.context().unwrap();
    let cases: serde_json::Value = serde_json::from_str(include_str!("repair.json")).unwrap();
    for c in cases.as_array().unwrap() {
        let mut g = wkb::read(&unhex(c["input"].as_str().unwrap())).unwrap();
        ctx.finish(&mut g, c["bad"].as_bool().unwrap(), 5.).unwrap();
        let expected = wkb::read_repaired(&unhex(c["output"].as_str().unwrap())).unwrap();
        assert_eq!(wkb::write(&g), wkb::write(&expected), "{}", c["name"]);
    }
}
