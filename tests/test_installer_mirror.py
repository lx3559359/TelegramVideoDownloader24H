import hashlib
import io
from threading import Event
import pytest
from urllib.request import Request
from tg_video_downloader import installer_update as m
from tg_video_downloader.paths import ProjectPaths

DATA=b'MZinstaller'
class Response(io.BytesIO):
    status=200
def release():
    return m.InstallerRelease('0.3.12',m.SITE+'/downloads/TelegramVideoDownloader-v0.3.12-Windows-x64-Setup.exe',len(DATA),hashlib.sha256(DATA).hexdigest(),'mirror')

@pytest.mark.parametrize('failure',[TimeoutError('idle'),ValueError('bad response'),None])
def test_auto_mirror_then_official_only_on_failure(tmp_path,failure):
    calls=[];sources=[]
    def open(request,**kwargs):
        calls.append(request.full_url)
        if 'modelscope.cn' in request.full_url and failure: raise failure
        return Response(DATA)
    manager=m.InstallerUpdateManager(ProjectPaths.from_root(tmp_path),current_version='0.3.11',opener=open)
    item=manager.download(release(),Event(),lambda *a:None,source_changed=sources.append)
    assert item.path.read_bytes()==DATA
    assert 'modelscope.cn' in calls[0]
    assert len(calls)==(2 if failure else 1)
    assert sources[-1]==('官网' if failure else '魔搭国内镜像')

def test_manual_official_and_cancel_do_not_use_mirror(tmp_path):
    calls=[]
    def open(request,**kw): calls.append(request.full_url);return Response(DATA)
    manager=m.InstallerUpdateManager(ProjectPaths.from_root(tmp_path),current_version='0.3.11',opener=open)
    manager.download(release(),Event(),lambda *a:None,source='official')
    assert calls==[release().url]
    cancel=Event();cancel.set()
    with pytest.raises(m.DownloadCancelled):manager.download(release(),cancel,lambda *a:None)
    assert len(calls)==1

@pytest.mark.parametrize('url',['http://cdn-lfs-cn-1.modelscope.cn/a','https://evil.com/a','https://cdn-lfs-cn-1.modelscope.cn.evil.com/a','https://user@cdn-lfs-cn-1.modelscope.cn/a'])
def test_mirror_redirect_rejects_untrusted_urls(url):
    with pytest.raises(ValueError):m.MirrorRedirect().redirect_request(Request('https://www.modelscope.cn/a'),None,302,'',{},url)

def test_mirror_redirect_allows_observed_https_cdn():
    url='https://cdn-lfs-cn-1.modelscope.cn/a?token=temporary'
    assert m.MirrorRedirect().redirect_request(Request('https://www.modelscope.cn/a'),None,302,'',{},url).full_url==url

def test_corrupt_mirror_is_not_installed(tmp_path):
    calls=[]
    def open(request,**kw):
        calls.append(request.full_url)
        return Response(b'x'*len(DATA) if 'modelscope.cn' in request.full_url else DATA)
    manager=m.InstallerUpdateManager(ProjectPaths.from_root(tmp_path),current_version='0.3.11',opener=open)
    assert manager.download(release(),Event(),lambda *a:None).path.read_bytes()==DATA
    assert len(calls)==2
    assert not list(tmp_path.rglob('*.part'))
