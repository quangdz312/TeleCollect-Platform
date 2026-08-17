import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import StaticPool

from src.models.db import (
    Dataset,
    DatasetEpisode,
    Episode,
    Task,
    User,
    enable_sqlite_foreign_keys,
    init_db,
    session_factory,
)
from src.models.enums import DatasetStatus, DemoOutcome, DemoStatus, UserRole


@pytest_asyncio.fixture
async def engine():
    """SQLite in-memory riêng cho mỗi test — StaticPool giữ 1 connection duy nhất
    để dữ liệu :memory: không biến mất giữa các session."""
    eng = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    enable_sqlite_foreign_keys(eng)
    await init_db(eng)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def session(engine):
    factory = session_factory(engine)
    async with factory() as s:
        yield s


async def _make_user(session, username: str, role: UserRole = UserRole.OPERATOR) -> User:
    user = User(username=username, password_hash="hashed", display_name=username, role=role)
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def _make_task(session, name: str = "pick_place") -> Task:
    task = Task(name=name, description="desc", action_dim=7, max_steps=200)
    session.add(task)
    await session.commit()
    await session.refresh(task)
    return task


@pytest.mark.asyncio
async def test_insert_and_query_users(session):
    user = await _make_user(session, "alice", UserRole.ADMIN)

    fetched = await session.get(User, user.id)
    assert fetched is not None
    assert fetched.username == "alice"
    assert fetched.role == UserRole.ADMIN
    assert fetched.is_active is True


@pytest.mark.asyncio
async def test_insert_and_query_tasks(session):
    await _make_task(session, "stack")

    fetched = await session.get(Task, "stack")
    assert fetched is not None
    assert fetched.action_dim == 7
    assert fetched.max_steps == 200


@pytest.mark.asyncio
async def test_insert_and_query_episodes(session):
    operator = await _make_user(session, "op1", UserRole.OPERATOR)
    task = await _make_task(session)

    episode = Episode(task_name=task.name, operator_id=operator.id)
    session.add(episode)
    await session.commit()
    await session.refresh(episode)

    fetched = await session.get(Episode, episode.id)
    assert fetched is not None
    assert fetched.status == DemoStatus.RECORDED
    assert fetched.task_name == "pick_place"


@pytest.mark.asyncio
async def test_insert_and_query_datasets(session):
    dataset = Dataset(name="v1", task_names=["pick_place"], include_failures=False)
    session.add(dataset)
    await session.commit()
    await session.refresh(dataset)

    fetched = await session.get(Dataset, dataset.id)
    assert fetched is not None
    assert fetched.status == DatasetStatus.BUILDING
    assert fetched.task_names == ["pick_place"]


@pytest.mark.asyncio
async def test_username_unique_constraint(session):
    await _make_user(session, "bob")

    session.add(User(username="bob", password_hash="x", display_name="bob2"))
    with pytest.raises(IntegrityError):
        await session.commit()


@pytest.mark.asyncio
async def test_dataset_name_unique_constraint(session):
    session.add(Dataset(name="v1", task_names=[]))
    await session.commit()

    session.add(Dataset(name="v1", task_names=[]))
    with pytest.raises(IntegrityError):
        await session.commit()


@pytest.mark.asyncio
async def test_task_name_unique_constraint(session):
    await _make_task(session, "push")

    session.add(Task(name="push", description="dup", action_dim=7, max_steps=100))
    with pytest.raises(IntegrityError):
        await session.commit()


@pytest.mark.asyncio
async def test_cascade_delete_dataset_removes_dataset_episodes(session):
    operator = await _make_user(session, "op2")
    task = await _make_task(session)
    episode = Episode(task_name=task.name, operator_id=operator.id)
    session.add(episode)
    await session.commit()
    await session.refresh(episode)

    dataset = Dataset(name="v2", task_names=[task.name])
    session.add(dataset)
    await session.commit()
    await session.refresh(dataset)

    session.add(DatasetEpisode(dataset_id=dataset.id, episode_id=episode.id))
    await session.commit()

    links = (await session.execute(select(DatasetEpisode))).scalars().all()
    assert len(links) == 1

    await session.delete(dataset)
    await session.commit()

    links_after = (await session.execute(select(DatasetEpisode))).scalars().all()
    assert links_after == []

    # episode gốc không bị đụng tới khi xoá dataset
    still_there = await session.get(Episode, episode.id)
    assert still_there is not None


@pytest.mark.asyncio
async def test_cascade_delete_episode_removes_dataset_episodes_not_dataset(session):
    operator = await _make_user(session, "op3")
    task = await _make_task(session)
    episode = Episode(task_name=task.name, operator_id=operator.id)
    session.add(episode)
    await session.commit()
    await session.refresh(episode)

    dataset = Dataset(name="v3", task_names=[task.name])
    session.add(dataset)
    await session.commit()
    await session.refresh(dataset)

    session.add(DatasetEpisode(dataset_id=dataset.id, episode_id=episode.id))
    await session.commit()

    await session.delete(episode)
    await session.commit()

    links_after = (await session.execute(select(DatasetEpisode))).scalars().all()
    assert links_after == []

    # dataset (bản snapshot zip) vẫn còn nguyên khi episode gốc bị xoá
    still_there = await session.get(Dataset, dataset.id)
    assert still_there is not None


@pytest.mark.asyncio
async def test_episode_operator_and_reviewer_point_to_correct_users(session):
    operator = await _make_user(session, "operator_x", UserRole.OPERATOR)
    reviewer = await _make_user(session, "reviewer_x", UserRole.REVIEWER)
    task = await _make_task(session)

    episode = Episode(
        task_name=task.name,
        operator_id=operator.id,
        reviewer_id=reviewer.id,
        status=DemoStatus.APPROVED,
        outcome=DemoOutcome.SUCCESS,
    )
    session.add(episode)
    await session.commit()
    await session.refresh(episode, attribute_names=["operator", "reviewer"])

    assert episode.operator.username == "operator_x"
    assert episode.reviewer.username == "reviewer_x"
    assert episode.operator_id == operator.id
    assert episode.reviewer_id == reviewer.id
