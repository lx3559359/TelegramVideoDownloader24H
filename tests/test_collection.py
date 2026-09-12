from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from tg_video_downloader.gateway import normalize_message
from tg_video_downloader.models import GroupTarget, JobSource, MessageInfo
from tg_video_downloader.naming import build_final_path
from tg_video_downloader.paths import ProjectPaths
from tg_video_downloader.search_ipc import _message_from_payload, _message_payload
from tg_video_downloader.state import StateStore
from tg_video_downloader.titles import extract_title
from tg_video_downloader.worker import DownloadWorker
from tests.fakes import FakeTelegramGateway


@pytest.mark.parametrize(('caption', 'expected'), [
    ('今日精选\n纪录片《山河之旅》上集\n剧情介绍：旅途故事', '山河之旅'),
    ('《山河之旅》下集 #山河之旅', '山河之旅'),
    ('⚡【山河之旅】新剧分享⚡', '山河之旅'),
    ('【高清】', None),
    ('片名：山河之旅，2024，高清完整版', '山河之旅'),
    ('片名: 山河之旅\n剧情介绍：旅行', '山河之旅'),
    ('《山河之旅》\n《山河之旅》', '山河之旅'),
    ('《山河之旅》与《星空》合集', None),
    ('普通说明 #纪录片', None),
    (None, None),
    ('《》', None),
])
def test_extract_title(caption, expected):
    assert extract_title(caption) == expected


def sample():
    return MessageInfo(-1001, 1, datetime(2026, 8, 1, tzinfo=UTC),
                       'video/mp4', 'part.mp4', '.mp4', 7, True, False, False,
                       collection_title='山河之旅')


def test_gateway_extracts_from_full_caption():
    raw = SimpleNamespace(id=1, date=datetime.now(UTC),
                          message='介绍' * 100 + '《山河之旅》',
                          document=None, video=object(), file=None)
    assert normalize_message(raw, chat_id=-1001).collection_title == '山河之旅'


def test_cross_month_and_unique_names(tmp_path):
    paths = ProjectPaths.from_root(tmp_path)
    first = build_final_path(paths, '群', sample())
    second = build_final_path(paths, '群', replace(sample(), message_id=2,
                              date=datetime(2026, 9, 1, tzinfo=UTC)))
    assert first.parent == second.parent == paths.downloads / '群_-1001' / '山河之旅'
    assert first != second
    unsafe = build_final_path(paths, '群', replace(sample(), collection_title='../escape'))
    assert unsafe.is_relative_to(paths.downloads)
    assert unsafe.parent.parent == paths.downloads / '群_-1001'


def test_ipc_title_roundtrip_and_legacy():
    payload = _message_payload(sample())
    assert _message_from_payload(payload) == sample()
    payload.pop('collection_title')
    assert _message_from_payload(payload).collection_title is None


def test_distinct_titles_do_not_collide_after_cleanup(tmp_path):
    paths = ProjectPaths.from_root(tmp_path)
    first = build_final_path(paths, '群', replace(sample(), collection_title='旅行:春天'))
    second = build_final_path(paths, '群', replace(sample(), collection_title='旅行?春天'))
    assert first.parent != second.parent


def test_existing_database_migrates_without_losing_jobs(tmp_path):
    database = tmp_path / 'state.db'
    state = StateStore(database)
    state.reconcile_targets((GroupTarget(-1001, '群'),))
    state.upsert_job(sample(), '群', JobSource.LIVE)
    state._connection.execute('ALTER TABLE jobs DROP COLUMN collection_title')
    state._connection.commit()
    state.close()
    state = StateStore(database)
    try:
        assert state.job_count() == 1
        assert state.get_job(-1001, 1).message.collection_title is None
    finally:
        state.close()


@pytest.mark.parametrize('manual', [False, True])
def test_title_persists_and_bound_job_keeps_title(tmp_path, manual):
    database = tmp_path / 'state.db'
    state = StateStore(database)
    group = GroupTarget(-1001, '群')
    state.reconcile_targets((group,))
    if manual:
        state.enqueue_manual_results(group, (sample(),))
    else:
        state.upsert_job(sample(), '群', JobSource.LIVE)
    state.close()
    state = StateStore(database)
    try:
        job = state.claim_next()
        assert job.message.collection_title == '山河之旅'
        state.bind_output_root(job, tmp_path / 'downloads')
        state.upsert_job(replace(sample(), collection_title='星空'), '群', JobSource.LIVE)
        assert state.get_job(-1001, 1).message.collection_title == '山河之旅'
    finally:
        state.close()


@pytest.mark.asyncio
async def test_worker_downloads_to_title_folder(tmp_path):
    paths = ProjectPaths.from_root(tmp_path)
    state = StateStore(tmp_path / 'state.db')
    state.reconcile_targets((GroupTarget(-1001, '群'),))
    state.upsert_job(sample(), '群', JobSource.LIVE)
    gateway = FakeTelegramGateway({})
    gateway.download_payloads[(-1001, 1)] = b'payload'
    try:
        assert await DownloadWorker(paths, state, gateway).run_one() == 'completed'
        assert (paths.downloads / '群_-1001' / '山河之旅' / '1_part.mp4').read_bytes() == b'payload'
    finally:
        state.close()


@pytest.mark.asyncio
async def test_album_title_uses_membership_not_nearby_messages(tmp_path):
    from tg_video_downloader.gateway import TelethonGateway
    from tg_video_downloader.models import Credentials
    from tests.test_gateway import LifecycleClient

    class Client(LifecycleClient):
        async def get_messages(self, chat_id, *, ids):
            assert chat_id == -1001
            assert len(ids) <= 21
            return [
                SimpleNamespace(grouped_id=77, message='【山河之旅】'),
                SimpleNamespace(grouped_id=88, message='【星空】'),
            ]

    gateway = TelethonGateway(ProjectPaths.from_root(tmp_path), Credentials(123, 'hash'),
                              client_factory=lambda *args, **kwargs: Client())
    await gateway.connect()
    try:
        blank = replace(sample(), collection_title=None, grouped_id=77)
        assert (await gateway.resolve_collection(blank)).collection_title == '山河之旅'
        unrelated = replace(blank, grouped_id=99)
        assert (await gateway.resolve_collection(unrelated)).collection_title is None
        standalone = replace(blank, grouped_id=None)
        assert await gateway.resolve_collection(standalone) == standalone
    finally:
        await gateway.disconnect()
