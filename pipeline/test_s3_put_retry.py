import hashlib

import pytest
import s3_put_retry as sp


class FlakyS3:
    "Fails the first ``fail`` upload_part calls, as the proxy does with a 520."

    def __init__(self, fail: int):
        self.fail, self.calls = fail, 0

    def upload_part(self, **kw):
        self.calls += 1
        if self.calls <= self.fail:
            raise RuntimeError("520")
        return {"ETag": f'"etag-{kw["PartNumber"]}"'}


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(sp.time, "sleep", lambda s: None)


def test_a_failed_part_is_retried_and_reads_its_own_slice(tmp_path, monkeypatch):
    monkeypatch.setattr(sp, "PART", 4)
    f = tmp_path / "x.bin"
    f.write_bytes(b"aaaabbbbcc")
    s3 = FlakyS3(fail=2)
    seen = []
    orig = s3.upload_part
    s3.upload_part = lambda **kw: (seen.append(kw["Body"]), orig(**kw))[1]
    assert sp.put_part(s3, "b", "k", "u", f, 2) == {"PartNumber": 2, "ETag": '"etag-2"'}
    assert s3.calls == 3
    assert seen == [b"bbbb"] * 3


def test_gives_up_after_the_attempt_limit(tmp_path):
    f = tmp_path / "x.bin"
    f.write_bytes(b"data")
    s3 = FlakyS3(fail=10**6)
    with pytest.raises(RuntimeError, match="520"):
        sp.put_part(s3, "b", "k", "u", f, 1)
    assert s3.calls == sp.ATTEMPTS


class FakeS3:
    """The multipart lifecycle as S3 runs it: the key holds nothing until a completion lands.

    ``complete_fail`` completion calls fail first, ``create_fail`` creates and ``head_fail``
    HEADs likewise, as the proxy does with a 520. ``remote_size`` makes the landed object
    report a size other than the one uploaded. The part bodies are kept and assembled in
    part order, so the landed object's size and ``body`` are what was really uploaded, and
    part ETags are real part md5s — the compound ETag the uploader derives from them is the
    one a landed object carries.
    """

    def __init__(self, complete_fail: int = 0, part_fail: bool = False, remote_size=None):
        self.complete_fail, self.part_fail, self.remote_size = complete_fail, part_fail, remote_size
        self.create_fail = self.head_fail = 0
        self.events = []
        self.completes = self.creates = self.heads = 0
        self.content_type = None
        self.bodies: dict[int, bytes] = {}
        self.object = None  # what the key holds: None until a completion lands
        self.body = None  # the bytes the key holds

    def create_multipart_upload(self, **kw):
        self.creates += 1
        if self.creates <= self.create_fail:
            raise RuntimeError("create 520")
        self.content_type = kw.get("ContentType")
        self.events.append("create")
        return {"UploadId": "u1"}

    def upload_part(self, **kw):
        if self.part_fail:
            raise RuntimeError("part 520")
        self.bodies[kw["PartNumber"]] = kw["Body"]
        return {"ETag": f'"{hashlib.md5(kw["Body"]).hexdigest()}"'}

    def _land(self, parts):
        numbers = [p["PartNumber"] for p in parts]
        if numbers != sorted(numbers):
            raise RuntimeError(f"InvalidPartOrder: {numbers}")  # as S3 answers
        self.body = b"".join(self.bodies[n] for n in numbers)
        size = len(self.body) if self.remote_size is None else self.remote_size
        self.object = {"ContentLength": size, "ETag": sp.multipart_etag(parts)}

    def complete_multipart_upload(self, **kw):
        self.completes += 1
        parts = kw["MultipartUpload"]["Parts"]
        if self.completes <= self.complete_fail:
            raise RuntimeError("complete 520")
        self._land(parts)
        self.events.append(("complete", [p["PartNumber"] for p in parts]))

    def abort_multipart_upload(self, **kw):
        self.events.append("abort")

    def head_object(self, **kw):
        self.heads += 1
        if self.heads <= self.head_fail:
            raise RuntimeError("head 520")
        if self.object is None:
            raise RuntimeError("NoSuchKey: 404")
        return dict(self.object)


class LostResponseS3(FakeS3):
    """S3 completes the upload, the proxy eats the 200, and the upload id is then gone.

    The failure the retry exists for, and the one a blind retry cannot survive:
    CompleteMultipartUpload is not idempotent, so every later call answers NoSuchUpload.
    """

    def complete_multipart_upload(self, **kw):
        self.completes += 1
        if self.completes == 1:
            self._land(kw["MultipartUpload"]["Parts"])
            self.events.append("completed-on-server")
            raise RuntimeError("520 from the proxy")
        raise RuntimeError("NoSuchUpload: the upload id does not exist")


def _file(tmp_path, monkeypatch, data=b"abcdefghij", name="x.bin"):
    monkeypatch.setattr(sp, "PART", 4)
    f = tmp_path / name
    f.write_bytes(data)
    return f


def test_upload_completes_all_parts_and_checks_the_size(tmp_path, monkeypatch):
    f = _file(tmp_path, monkeypatch)
    s3 = FakeS3()
    assert sp.upload(s3, f, "b", "k", workers=2) == (3, 10)
    assert s3.events == ["create", ("complete", [1, 2, 3])]
    # The object the parts assembled into, not the number the uploader was told to expect.
    assert s3.body == b"abcdefghij"


def test_complete_is_retried(tmp_path, monkeypatch):
    f = _file(tmp_path, monkeypatch)
    s3 = FakeS3(complete_fail=2)
    sp.upload(s3, f, "b", "k")
    assert s3.completes == 3
    assert "abort" not in s3.events


def test_failed_part_aborts_the_upload(tmp_path, monkeypatch):
    f = _file(tmp_path, monkeypatch)
    s3 = FakeS3(part_fail=True)
    with pytest.raises(RuntimeError, match="520"):
        sp.upload(s3, f, "b", "k")
    assert s3.events == ["create", "abort"]


def test_complete_that_never_succeeds_aborts(tmp_path, monkeypatch):
    f = _file(tmp_path, monkeypatch)
    s3 = FakeS3(complete_fail=10**6)
    with pytest.raises(RuntimeError, match="complete 520"):
        sp.upload(s3, f, "b", "k")
    assert s3.completes == sp.ATTEMPTS
    assert s3.events[-1] == "abort"


def test_a_failing_abort_does_not_hide_why_the_upload_failed(tmp_path, monkeypatch):
    """The abort goes through the same failing proxy, so it must not become the error raised.

    Otherwise the operator reading the job log sees "abort 520" and never learns which
    part failed or why, which is the one thing this module is for.
    """
    f = _file(tmp_path, monkeypatch)

    class AbortAlsoFailsS3(FakeS3):
        def abort_multipart_upload(self, **kw):
            self.events.append("abort-failed")
            raise RuntimeError("abort 520 from the proxy")

    s3 = AbortAlsoFailsS3(part_fail=True)
    with pytest.raises(RuntimeError, match="part 520"):
        sp.upload(s3, f, "b", "k")
    assert s3.events == ["create", "abort-failed"]


def test_empty_file_is_refused_before_creating_an_upload(tmp_path, monkeypatch):
    f = _file(tmp_path, monkeypatch, data=b"")
    s3 = FakeS3()
    with pytest.raises(SystemExit, match="empty file"):
        sp.upload(s3, f, "b", "k")
    assert s3.events == []


def test_a_lost_completion_response_is_verified_against_the_object(tmp_path, monkeypatch):
    "The completing call is not idempotent: a second call 404s, so the object has the answer."
    f = _file(tmp_path, monkeypatch)
    s3 = LostResponseS3()
    assert sp.upload(s3, f, "b", "k") == (3, 10)
    # One call, then the HEAD that found the object complete: no retry burns on NoSuchUpload.
    assert s3.completes == 1
    assert "abort" not in s3.events
    assert s3.body == b"abcdefghij"


def test_another_upload_of_the_same_size_is_not_mistaken_for_this_one(tmp_path, monkeypatch):
    "A key that already holds a same-sized object must not read as this upload landing."
    f = _file(tmp_path, monkeypatch)
    s3 = FakeS3(complete_fail=10**6)
    s3.object = {"ContentLength": 10, "ETag": '"' + "0" * 32 + '-3"'}
    with pytest.raises(RuntimeError, match="complete 520"):
        sp.upload(s3, f, "b", "k")
    assert s3.completes == sp.ATTEMPTS
    assert s3.events[-1] == "abort"


def test_create_is_retried(tmp_path, monkeypatch):
    f = _file(tmp_path, monkeypatch)
    s3 = FakeS3()
    s3.create_fail = 1
    assert sp.upload(s3, f, "b", "k") == (3, 10)
    assert s3.creates == 2


def test_the_verifying_head_is_retried(tmp_path, monkeypatch):
    "A 520 on the HEAD must not fail an upload whose bytes are already in the bucket."
    f = _file(tmp_path, monkeypatch)
    s3 = FakeS3()
    s3.head_fail = 1
    assert sp.upload(s3, f, "b", "k") == (3, 10)
    assert s3.heads == 2


def test_the_content_type_is_the_one_the_catalog_declares(tmp_path, monkeypatch):
    f = _file(tmp_path, monkeypatch, name="fields-2025.pmtiles")
    s3 = FakeS3()
    sp.upload(s3, f, "b", "k")
    assert s3.content_type == "application/vnd.pmtiles"

    g = _file(tmp_path, monkeypatch, name="cells_a5r7_2025.parquet")
    s3 = FakeS3()
    sp.upload(s3, g, "b", "k")
    assert s3.content_type == "application/vnd.apache.parquet"


def test_an_explicit_content_type_wins(tmp_path, monkeypatch):
    f = _file(tmp_path, monkeypatch, name="fields-2025.pmtiles")
    s3 = FakeS3()
    sp.upload(s3, f, "b", "k", content_type="application/octet-stream")
    assert s3.content_type == "application/octet-stream"


def test_remote_size_mismatch_is_an_error(tmp_path, monkeypatch):
    f = _file(tmp_path, monkeypatch)
    s3 = FakeS3(remote_size=7)
    with pytest.raises(SystemExit, match="remote size 7 != local 10"):
        sp.upload(s3, f, "b", "k")
