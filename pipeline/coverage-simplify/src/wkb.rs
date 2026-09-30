use crate::geom::{Geometry, Point, Polygon};
use anyhow::{Result, bail, ensure};
struct Reader<'a> {
    bytes: &'a [u8],
    pos: usize,
    le: bool,
}
impl Reader<'_> {
    fn take<const N: usize>(&mut self) -> Result<[u8; N]> {
        ensure!(self.pos + N <= self.bytes.len(), "truncated WKB");
        let a = self.bytes[self.pos..self.pos + N].try_into().unwrap();
        self.pos += N;
        Ok(a)
    }
    fn u32(&mut self) -> Result<u32> {
        let b = self.take()?;
        Ok(if self.le {
            u32::from_le_bytes(b)
        } else {
            u32::from_be_bytes(b)
        })
    }
    fn f64(&mut self) -> Result<f64> {
        let b = self.take()?;
        Ok(if self.le {
            f64::from_le_bytes(b)
        } else {
            f64::from_be_bytes(b)
        })
    }
    fn geom(&mut self) -> Result<Geometry> {
        self.le = match self.take::<1>()?[0] {
            0 => false,
            1 => true,
            _ => bail!("invalid WKB endian"),
        };
        let t = self.u32()?;
        ensure!(
            t == 3 || t == 6,
            "only 2D Polygon/MultiPolygon WKB supported, got {t}"
        );
        let n = self.u32()? as usize;
        ensure!(
            n <= self.bytes.len().saturating_sub(self.pos) / 4,
            "invalid WKB count"
        );
        if t == 6 {
            let mut polygons = Vec::with_capacity(n);
            for _ in 0..n {
                let g = self.geom()?;
                ensure!(!g.multi, "nested MultiPolygon");
                polygons.extend(g.polygons);
            }
            return Ok(Geometry {
                polygons,
                multi: true,
                encoded: None,
            });
        }
        let mut p: Polygon = Vec::with_capacity(n);
        for _ in 0..n {
            let m = self.u32()? as usize;
            ensure!(
                m <= self.bytes.len().saturating_sub(self.pos) / 16,
                "invalid ring size"
            );
            let mut r = Vec::with_capacity(m);
            for _ in 0..m {
                let q = Point {
                    x: self.f64()?,
                    y: self.f64()?,
                };
                ensure!(q.x.is_finite() && q.y.is_finite(), "nonfinite coordinate");
                r.push(q);
            }
            ensure!(
                r.is_empty() || (r.len() >= 3 && r.first() == r.last()),
                "unclosed or short ring"
            );
            p.push(r);
        }
        Ok(Geometry {
            polygons: vec![p],
            multi: false,
            encoded: None,
        })
    }
}
pub fn read(b: &[u8]) -> Result<Geometry> {
    let mut r = Reader {
        bytes: b,
        pos: 0,
        le: true,
    };
    let g = r.geom()?;
    ensure!(r.pos == b.len(), "trailing WKB");
    Ok(g)
}
fn num(b: &mut Vec<u8>, v: usize) {
    b.extend_from_slice(&(v as u32).to_le_bytes());
}
fn polygon(b: &mut Vec<u8>, p: &Polygon) {
    b.push(1);
    num(b, 3);
    num(b, p.len());
    for r in p {
        num(b, r.len());
        for q in r {
            b.extend_from_slice(&q.x.to_le_bytes());
            b.extend_from_slice(&q.y.to_le_bytes());
        }
    }
}
pub fn write(g: &Geometry) -> Vec<u8> {
    if let Some((template, offsets)) = &g.encoded {
        let mut b = template.clone();
        for (p, &(i, le)) in g.points().zip(offsets) {
            b[i..i + 8].copy_from_slice(&if le {
                p.x.to_le_bytes()
            } else {
                p.x.to_be_bytes()
            });
            b[i + 8..i + 16].copy_from_slice(&if le {
                p.y.to_le_bytes()
            } else {
                p.y.to_be_bytes()
            });
        }
        return b;
    }
    let mut b = Vec::new();
    if g.multi {
        b.push(1);
        num(&mut b, 6);
        num(&mut b, g.polygons.len());
        for p in &g.polygons {
            polygon(&mut b, p);
        }
    } else {
        polygon(&mut b, g.polygons.first().unwrap_or(&Vec::new()));
    }
    b
}
/// GEOS make_valid can return only lines/points. Python _polygonal retains those.
/// Keep the exact WKB structure, exposing only XY coordinates for inverse projection.
pub fn read_repaired(b: &[u8]) -> Result<Geometry> {
    if let Ok(g) = read(b) {
        return Ok(g);
    }
    fn collect(
        r: &mut Reader<'_>,
        pts: &mut Vec<Point>,
        offsets: &mut Vec<(usize, bool)>,
    ) -> Result<()> {
        r.le = match r.take::<1>()?[0] {
            0 => false,
            1 => true,
            _ => bail!("invalid endian"),
        };
        let t = r.u32()?;
        let count = if t == 1 { 1 } else { r.u32()? as usize };
        ensure!(count <= r.bytes.len(), "invalid geometry size");
        let mut coordinates = |r: &mut Reader<'_>, n: usize| -> Result<()> {
            ensure!(
                n <= r.bytes.len().saturating_sub(r.pos) / 16,
                "truncated coordinates"
            );
            for _ in 0..n {
                let offset = r.pos;
                let p = Point {
                    x: r.f64()?,
                    y: r.f64()?,
                };
                if t == 1 && p.x.is_nan() && p.y.is_nan() {
                    continue;
                }
                ensure!(
                    p.x.is_finite() && p.y.is_finite(),
                    "nonfinite repaired geometry"
                );
                pts.push(p);
                offsets.push((offset, r.le));
            }
            Ok(())
        };
        match t {
            1 | 2 => coordinates(r, count)?,
            3 => {
                for _ in 0..count {
                    let n = r.u32()? as usize;
                    coordinates(r, n)?;
                }
            }
            4..=7 => {
                for _ in 0..count {
                    collect(r, pts, offsets)?;
                }
            }
            _ => bail!("unsupported repaired WKB type {t}"),
        }
        Ok(())
    }
    let mut r = Reader {
        bytes: b,
        pos: 0,
        le: true,
    };
    let mut pts = vec![];
    let mut offsets = vec![];
    collect(&mut r, &mut pts, &mut offsets)?;
    ensure!(r.pos == b.len(), "trailing repaired WKB");
    Ok(Geometry {
        polygons: vec![vec![pts]],
        multi: false,
        encoded: Some((b.to_vec(), offsets)),
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn roundtrip_and_bad_input() {
        let g = Geometry {
            polygons: vec![vec![vec![
                Point { x: 0., y: 0. },
                Point { x: 1., y: 0. },
                Point { x: 0., y: 1. },
                Point { x: 0., y: 0. },
            ]]],
            multi: true,
            encoded: None,
        };
        assert_eq!(write(&read(&write(&g)).unwrap()), write(&g));
        assert!(read(&[1, 3, 0, 0, 0, 255, 255, 255, 255]).is_err());
    }
}
