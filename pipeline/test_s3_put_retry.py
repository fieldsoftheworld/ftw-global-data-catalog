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
    "Records the multipart lifecycle; ``complete_fail`` completion calls fail first."

    def __init__(self, complete_fail: int = 0, part_fail: bool = False, remote_size=None):
        self.complete_fail, self.part_fail, self.remote_size = complete_fail, part_fail, remote_size
        self.events, self.completes = [], 0
        self.size = None

    def create_multipart_upload(self, **kw):
        self.events.append("create")
        return {"UploadId": "u1"}

    def upload_part(self, **kw):
        if self.part_fail:
            raise RuntimeError("part 520")
        return {"ETag": f'"e{kw["PartNumber"]}"'}

    def complete_multipart_upload(self, **kw):
        self.completes += 1
        if self.completes <= self.complete_fail:
            raise RuntimeError("complete 520")
        self.events.append(("complete", [p["PartNumber"] for p in kw["MultipartUpload"]["Parts"]]))

    def abort_multipart_upload(self, **kw):
        self.events.append("abort")

    def head_object(self, **kw):
        return {"ContentLength": self.size if self.remote_size is None else self.remote_size}


def _file(tmp_path, monkeypatch, data=b"abcdefghij"):
    monkeypatch.setattr(sp, "PART", 4)
    f = tmp_path / "x.bin"
    f.write_bytes(data)
    return f


def test_upload_completes_all_parts_and_checks_the_size(tmp_path, monkeypatch):
    f = _file(tmp_path, monkeypatch)
    s3 = FakeS3()
    s3.size = 10
    assert sp.upload(s3, f, "b", "k", workers=2) == (3, 10)
    assert s3.events == ["create", ("complete", [1, 2, 3])]


def test_complete_is_retried(tmp_path, monkeypatch):
    f = _file(tmp_path, monkeypatch)
    s3 = FakeS3(complete_fail=2)
    s3.size = 10
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


def test_empty_file_is_refused_before_creating_an_upload(tmp_path, monkeypatch):
    f = _file(tmp_path, monkeypatch, data=b"")
    s3 = FakeS3()
    with pytest.raises(SystemExit, match="empty file"):
        sp.upload(s3, f, "b", "k")
    assert s3.events == []


def test_remote_size_mismatch_is_an_error(tmp_path, monkeypatch):
    f = _file(tmp_path, monkeypatch)
    s3 = FakeS3(remote_size=7)
    with pytest.raises(SystemExit, match="remote size 7 != local 10"):
        sp.upload(s3, f, "b", "k")
