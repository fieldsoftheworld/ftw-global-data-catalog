//! Narrow, runtime-loaded C APIs. Each worker owns its own PROJ/GEOS context.
//! Library owners outlive function pointers; GEOS outputs use matching free calls.
use crate::{geom::Geometry, wkb};
use anyhow::{Context, Result, bail, ensure};
use libloading::Library;
use std::{
    ffi::{CString, c_char, c_int, c_void},
    path::Path,
    ptr,
};
type Ptr = *mut c_void;
unsafe fn symbol<T: Copy>(lib: &Library, name: &[u8]) -> Result<T> {
    // SAFETY: all symbol types below match the pinned libraries' C headers.
    Ok(unsafe { *lib.get::<T>(name)? })
}
pub struct ProjApi {
    _lib: Library,
    create: unsafe extern "C" fn() -> Ptr,
    destroy_context: unsafe extern "C" fn(Ptr),
    create_crs: unsafe extern "C" fn(Ptr, *const c_char, *const c_char, Ptr) -> Ptr,
    normalize: unsafe extern "C" fn(Ptr, Ptr) -> Ptr,
    destroy: unsafe extern "C" fn(Ptr) -> Ptr,
    search: unsafe extern "C" fn(Ptr, c_int, *const *const c_char),
    trans: unsafe extern "C" fn(Ptr, c_int, usize, *mut [f64; 4]) -> c_int,
}
impl ProjApi {
    pub fn load(path: &Path) -> Result<Self> {
        unsafe {
            let l = Library::new(path)?;
            Ok(Self {
                create: symbol(&l, b"proj_context_create")?,
                destroy_context: symbol(&l, b"proj_context_destroy")?,
                create_crs: symbol(&l, b"proj_create_crs_to_crs")?,
                normalize: symbol(&l, b"proj_normalize_for_visualization")?,
                destroy: symbol(&l, b"proj_destroy")?,
                search: symbol(&l, b"proj_context_set_search_paths")?,
                trans: symbol(&l, b"proj_trans_array")?,
                _lib: l,
            })
        }
    }
    pub fn transformer(&self, epsg: u32, data: &Path) -> Result<Projection<'_>> {
        unsafe {
            let ctx = (self.create)();
            ensure!(!ctx.is_null(), "PROJ context allocation failed");
            let data = CString::new(data.to_string_lossy().as_bytes())?;
            let paths = [data.as_ptr()];
            (self.search)(ctx, 1, paths.as_ptr());
            let dst = CString::new(format!("EPSG:{epsg}"))?;
            let raw = (self.create_crs)(ctx, c"EPSG:4326".as_ptr(), dst.as_ptr(), ptr::null_mut());
            if raw.is_null() {
                (self.destroy_context)(ctx);
                bail!("PROJ CRS transform creation failed");
            }
            let pj = (self.normalize)(ctx, raw);
            (self.destroy)(raw);
            if pj.is_null() {
                (self.destroy_context)(ctx);
                bail!("PROJ axis normalization failed");
            }
            Ok(Projection { api: self, ctx, pj })
        }
    }
}
pub struct Projection<'a> {
    api: &'a ProjApi,
    ctx: Ptr,
    pj: Ptr,
}
impl Projection<'_> {
    pub fn apply(&self, g: &mut Geometry, inverse: bool) -> Result<()> {
        let mut coords: Vec<[f64; 4]> = g.points().map(|p| [p.x, p.y, 0., f64::INFINITY]).collect();
        let err = unsafe {
            (self.api.trans)(
                self.pj,
                if inverse { -1 } else { 1 },
                coords.len(),
                coords.as_mut_ptr(),
            )
        };
        ensure!(err == 0, "PROJ transformation failed with code {err}");
        for (p, c) in g.points_mut().zip(coords) {
            ensure!(
                c[0].is_finite() && c[1].is_finite(),
                "nonfinite PROJ result"
            );
            p.x = c[0];
            p.y = c[1];
        }
        Ok(())
    }
}
impl Drop for Projection<'_> {
    fn drop(&mut self) {
        unsafe {
            (self.api.destroy)(self.pj);
            (self.api.destroy_context)(self.ctx);
        }
    }
}

pub struct GeosApi {
    _lib: Library,
    init: unsafe extern "C" fn() -> Ptr,
    finish: unsafe extern "C" fn(Ptr),
    read: unsafe extern "C" fn(Ptr, *const u8, usize) -> Ptr,
    write: unsafe extern "C" fn(Ptr, Ptr, *mut usize) -> *mut u8,
    free: unsafe extern "C" fn(Ptr, Ptr),
    destroy: unsafe extern "C" fn(Ptr, Ptr),
    valid: unsafe extern "C" fn(Ptr, Ptr) -> c_char,
    make_valid: unsafe extern "C" fn(Ptr, Ptr) -> Ptr,
    simplify: unsafe extern "C" fn(Ptr, Ptr, f64) -> Ptr,
    kind: unsafe extern "C" fn(Ptr, Ptr) -> c_int,
    count: unsafe extern "C" fn(Ptr, Ptr) -> c_int,
    child: unsafe extern "C" fn(Ptr, Ptr, c_int) -> Ptr,
    clone: unsafe extern "C" fn(Ptr, Ptr) -> Ptr,
    collection: unsafe extern "C" fn(Ptr, c_int, *mut Ptr, u32) -> Ptr,
    union: unsafe extern "C" fn(Ptr, Ptr) -> Ptr,
    coverage_valid: unsafe extern "C" fn(Ptr, Ptr, f64, *mut Ptr) -> c_int,
    empty: unsafe extern "C" fn(Ptr, Ptr) -> c_char,
}
impl GeosApi {
    pub fn load(path: &Path) -> Result<Self> {
        unsafe {
            let l = Library::new(path)?;
            let version: unsafe extern "C" fn() -> *const c_char = symbol(&l, b"GEOSversion")?;
            let v = std::ffi::CStr::from_ptr(version()).to_str()?;
            ensure!(v.starts_with("3.13.1-"), "GEOS 3.13.1 required, found {v}");
            Ok(Self {
                init: symbol(&l, b"GEOS_init_r")?,
                finish: symbol(&l, b"GEOS_finish_r")?,
                read: symbol(&l, b"GEOSGeomFromWKB_buf_r")?,
                write: symbol(&l, b"GEOSGeomToWKB_buf_r")?,
                free: symbol(&l, b"GEOSFree_r")?,
                destroy: symbol(&l, b"GEOSGeom_destroy_r")?,
                valid: symbol(&l, b"GEOSisValid_r")?,
                make_valid: symbol(&l, b"GEOSMakeValid_r")?,
                simplify: symbol(&l, b"GEOSTopologyPreserveSimplify_r")?,
                kind: symbol(&l, b"GEOSGeomTypeId_r")?,
                count: symbol(&l, b"GEOSGetNumGeometries_r")?,
                child: symbol(&l, b"GEOSGetGeometryN_r")?,
                clone: symbol(&l, b"GEOSGeom_clone_r")?,
                collection: symbol(&l, b"GEOSGeom_createCollection_r")?,
                union: symbol(&l, b"GEOSUnaryUnion_r")?,
                coverage_valid: symbol(&l, b"GEOSCoverageIsValid_r")?,
                empty: symbol(&l, b"GEOSisEmpty_r")?,
                _lib: l,
            })
        }
    }
    pub fn context(&self) -> Result<Geos<'_>> {
        let ctx = unsafe { (self.init)() };
        ensure!(!ctx.is_null(), "GEOS context allocation failed");
        Ok(Geos { api: self, ctx })
    }
}
pub struct Geos<'a> {
    api: &'a GeosApi,
    ctx: Ptr,
}
struct Owned<'a, 'b> {
    geos: &'a Geos<'b>,
    p: Ptr,
}
impl Drop for Owned<'_, '_> {
    fn drop(&mut self) {
        unsafe {
            (self.geos.api.destroy)(self.geos.ctx, self.p);
        }
    }
}
impl Geos<'_> {
    fn own(&self, p: Ptr) -> Result<Owned<'_, '_>> {
        ensure!(!p.is_null(), "GEOS operation returned NULL");
        Ok(Owned { geos: self, p })
    }
    fn read(&self, g: &Geometry) -> Result<Owned<'_, '_>> {
        let b = wkb::write(g);
        self.own(unsafe { (self.api.read)(self.ctx, b.as_ptr(), b.len()) })
    }
    fn decode(&self, g: Ptr) -> Result<Geometry> {
        let mut n = 0;
        let p = unsafe { (self.api.write)(self.ctx, g, &mut n) };
        ensure!(!p.is_null(), "GEOS WKB write failed");
        let result = wkb::read_repaired(unsafe { std::slice::from_raw_parts(p, n) });
        unsafe {
            (self.api.free)(self.ctx, p.cast());
        }
        result
    }
    pub fn finish(&self, g: &mut Geometry, bad: bool, tolerance: f64) -> Result<bool> {
        let mut p = self.read(g)?;
        if bad {
            p = self.own(unsafe { (self.api.simplify)(self.ctx, p.p, tolerance.min(1.2)) })?;
        }
        let valid = unsafe { (self.api.valid)(self.ctx, p.p) };
        ensure!(valid == 0 || valid == 1, "GEOS validity check failed");
        if valid == 0 {
            p = self.own(unsafe { (self.api.make_valid)(self.ctx, p.p) })?;
            let kind = unsafe { (self.api.kind)(self.ctx, p.p) };
            if kind != 3 && kind != 6 {
                let mut parts = vec![];
                for i in 0..unsafe { (self.api.count)(self.ctx, p.p) } {
                    let child = unsafe { (self.api.child)(self.ctx, p.p, i) };
                    if matches!(unsafe { (self.api.kind)(self.ctx, child) }, 3 | 6) {
                        let cloned = unsafe { (self.api.clone)(self.ctx, child) };
                        ensure!(!cloned.is_null(), "GEOS clone failed");
                        parts.push(cloned);
                    }
                }
                if parts.is_empty() {
                    *g = self.decode(p.p)?;
                    return Ok(true);
                }
                let collection = self.own(unsafe {
                    (self.api.collection)(self.ctx, 7, parts.as_mut_ptr(), parts.len() as u32)
                })?;
                p = self.own(unsafe { (self.api.union)(self.ctx, collection.p) })?;
            }
        }
        if bad || valid == 0 {
            *g = self.decode(p.p).context("GEOS result decoding")?;
        }
        Ok(valid == 0)
    }
    /// Independent GEOS oracle for tests/audits, never used by the hot path.
    pub fn invalid_mask(&self, geoms: &[Geometry]) -> Result<Vec<bool>> {
        let mut parts = Vec::new();
        for g in geoms {
            let p = self.read(g)?;
            parts.push(p.p);
            std::mem::forget(p);
        }
        let coll = self.own(unsafe {
            (self.api.collection)(self.ctx, 7, parts.as_mut_ptr(), parts.len() as u32)
        })?;
        let mut out = ptr::null_mut();
        let code = unsafe { (self.api.coverage_valid)(self.ctx, coll.p, 0., &mut out) };
        ensure!(code == 0 || code == 1, "GEOS coverage oracle failed");
        let result = self.own(out)?;
        (0..geoms.len())
            .map(|i| {
                let p = unsafe { (self.api.child)(self.ctx, result.p, i as c_int) };
                ensure!(!p.is_null(), "missing GEOS coverage result");
                let e = unsafe { (self.api.empty)(self.ctx, p) };
                ensure!(e == 0 || e == 1, "GEOS is_empty failed");
                Ok(e == 0)
            })
            .collect()
    }
}
impl Drop for Geos<'_> {
    fn drop(&mut self) {
        unsafe {
            (self.api.finish)(self.ctx);
        }
    }
}
