use crate::{geom::Geometry, wkb};
use anyhow::{Context, Result, ensure};
use arrow_array::{Array, ArrayRef, BinaryArray, Float64Array, LargeBinaryArray, RecordBatch};
use arrow_schema::Schema;
use parquet::{
    arrow::{ArrowWriter, arrow_reader::ParquetRecordBatchReaderBuilder},
    basic::{Compression, ZstdLevel},
    file::{metadata::KeyValue, properties::WriterProperties},
};
use rayon::prelude::*;
use std::{
    fs::{self, File},
    os::unix::fs::MetadataExt,
    path::Path,
    sync::Arc,
};

pub struct Input {
    pub batches: Vec<RecordBatch>,
    pub schema: Arc<Schema>,
    pub geoms: Vec<Geometry>,
    pub key_values: Vec<KeyValue>,
}
pub fn fingerprint(src: &Path, tolerance: f64) -> Result<String> {
    let st = src.metadata()?;
    let ns = st.mtime() as i128 * 1_000_000_000 + st.mtime_nsec() as i128;
    Ok(format!(
        "tol={};size={};mtime_ns={ns}",
        python_float(tolerance),
        st.len()
    ))
}
pub fn current(dst: &Path, fp: &str) -> bool {
    let Ok(f) = File::open(dst) else {
        return false;
    };
    let Ok(reader) = ParquetRecordBatchReaderBuilder::try_new(f) else {
        return false;
    };
    reader
        .schema()
        .metadata()
        .get("simplify_fingerprint")
        .is_some_and(|v| v == fp)
}
pub fn read(src: &Path) -> Result<Input> {
    let reader = ParquetRecordBatchReaderBuilder::try_new(File::open(src)?)?;
    let schema = reader.schema().clone();
    let key_values = reader
        .metadata()
        .file_metadata()
        .key_value_metadata()
        .cloned()
        .unwrap_or_default();
    let col = schema.index_of("geometry")?;
    let batches: Vec<_> = reader
        .with_batch_size(16384)
        .build()?
        .collect::<std::result::Result<_, _>>()?;
    let geoms: Vec<Vec<Geometry>> = batches
        .par_iter()
        .map(|b| {
            let a = b.column(col);
            ensure!(a.null_count() == 0, "NULL geometry unsupported");
            if let Some(a) = a.as_any().downcast_ref::<BinaryArray>() {
                (0..a.len()).map(|i| wkb::read(a.value(i))).collect()
            } else if let Some(a) = a.as_any().downcast_ref::<LargeBinaryArray>() {
                (0..a.len()).map(|i| wkb::read(a.value(i))).collect()
            } else {
                anyhow::bail!("geometry column must be binary WKB")
            }
        })
        .collect::<Result<_>>()?;
    Ok(Input {
        batches,
        schema,
        geoms: geoms.into_iter().flatten().collect(),
        key_values,
    })
}
pub fn write(input: Input, dst: &Path, fp: String) -> Result<()> {
    let parent = dst.parent().context("destination without parent")?;
    fs::create_dir_all(parent)?;
    let tmp = dst.with_file_name(format!(
        "{}.tmp-{}",
        dst.file_name().unwrap().to_string_lossy(),
        std::process::id()
    ));
    let mut metadata = input.schema.metadata().clone();
    metadata.insert("simplify_fingerprint", fp.clone());
    let schema = Arc::new(Schema::new_with_metadata(
        input.schema.fields().clone(),
        metadata,
    ));
    let mut footer: std::collections::BTreeMap<String, Option<String>> = input
        .key_values
        .into_iter()
        .filter(|v| v.key != "ARROW:schema")
        .map(|v| (v.key, v.value))
        .collect();
    for (k, v) in schema.metadata().iter() {
        footer.insert(k.to_owned(), Some(v.to_owned()));
    }
    let props = WriterProperties::builder()
        .set_compression(Compression::ZSTD(ZstdLevel::default()))
        .set_max_row_group_row_count(Some(50_000))
        .set_key_value_metadata(Some(
            footer
                .into_iter()
                .map(|(k, v)| KeyValue::new(k, v))
                .collect(),
        ))
        .build();
    let file = File::options().write(true).create_new(true).open(&tmp)?;
    let mut writer = ArrowWriter::try_new(file, schema.clone(), Some(props))?;
    let mut offset = 0;
    for b in input.batches {
        let g = &input.geoms[offset..offset + b.num_rows()];
        offset += b.num_rows();
        let mut cols = b.columns().to_vec();
        let bytes: Vec<_> = g.par_iter().map(wkb::write).collect();
        let gi = schema.index_of("geometry")?;
        cols[gi] = if b.column(gi).as_any().is::<LargeBinaryArray>() {
            Arc::new(LargeBinaryArray::from_iter_values(
                bytes.iter().map(Vec::as_slice),
            )) as ArrayRef
        } else {
            Arc::new(BinaryArray::from_iter_values(
                bytes.iter().map(Vec::as_slice),
            )) as ArrayRef
        };
        let bounds: Vec<_> = g.par_iter().map(Geometry::bounds).collect();
        for (j, name) in ["xmin", "ymin", "xmax", "ymax"].iter().enumerate() {
            let values: Vec<_> = bounds
                .iter()
                .map(|b| {
                    if b.empty() {
                        f64::NAN
                    } else if j < 2 {
                        b.min[j]
                    } else {
                        b.max[j - 2]
                    }
                })
                .collect();
            cols[schema.index_of(name)?] = Arc::new(Float64Array::from(values));
        }
        writer.write(&RecordBatch::try_new(schema.clone(), cols)?)?;
    }
    writer.into_inner()?.sync_all()?;
    fs::rename(&tmp, dst)?;
    File::open(parent)?.sync_all()?;
    Ok(())
}

/// Python repr(float) pads exponent digits and includes a positive exponent sign.
fn python_float(value: f64) -> String {
    let text = format!("{value:?}");
    if let Some((base, exponent)) = text.split_once('e') {
        let exponent: i32 = exponent.parse().unwrap();
        format!("{base}e{exponent:+03}")
    } else {
        text
    }
}
#[cfg(test)]
mod tests {
    use super::python_float;
    #[test]
    fn fingerprints_use_python_float_format() {
        for (x, expected) in [
            (5., "5.0"),
            (1e-5, "1e-05"),
            (1e20, "1e+20"),
            (-0., "-0.0"),
            (1e-4, "0.0001"),
        ] {
            assert_eq!(python_float(x), expected);
        }
    }
}
