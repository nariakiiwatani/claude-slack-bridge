"""データモデル（Task, Session, Project）のテスト。"""

import bridge

# bridge.TaskStatus を直接参照する（from bridge import すると別オブジェクトになる可能性がある）
TaskStatus = bridge.TaskStatus


# ── Task ──────────────────────────────────────────────────

class TestTask:
    def test_short_id(self, make_task):
        task = make_task(id=42)
        assert task.short_id == "#42"

    def test_short_id_one(self, make_task):
        task = make_task(id=1)
        assert task.short_id == "#1"

    def test_default_status_is_queued(self, make_task):
        task = make_task()
        assert task.status == TaskStatus.QUEUED

    def test_default_fields(self, make_task):
        task = make_task()
        assert task.result is None
        assert task.error is None
        assert task.started_at is None
        assert task.completed_at is None
        assert task.tool_calls == []
        assert task.process is None
        assert task.master_fd is None
        assert task.resume_session is None


# ── Session ───────────────────────────────────────────────

class TestSession:
    def test_active_task_queued(self, make_session, make_task):
        session = make_session()
        t = make_task(status=TaskStatus.QUEUED)
        session.tasks.append(t)
        assert session.active_task is t

    def test_active_task_running(self, make_session, make_task):
        session = make_session()
        t = make_task(status=TaskStatus.RUNNING)
        session.tasks.append(t)
        assert session.active_task is t

    def test_active_task_none_when_completed(self, make_session, make_task):
        session = make_session()
        t = make_task(status=TaskStatus.COMPLETED)
        session.tasks.append(t)
        assert session.active_task is None

    def test_active_task_none_when_empty(self, make_session):
        session = make_session()
        assert session.active_task is None

    def test_active_task_returns_first_active(self, make_session, make_task):
        session = make_session()
        t1 = make_task(id=1, status=TaskStatus.COMPLETED)
        t2 = make_task(id=2, status=TaskStatus.RUNNING)
        t3 = make_task(id=3, status=TaskStatus.QUEUED)
        session.tasks.extend([t1, t2, t3])
        assert session.active_task is t2

    def test_latest_task(self, make_session, make_task):
        session = make_session()
        t1 = make_task(id=1)
        t2 = make_task(id=2)
        session.tasks.extend([t1, t2])
        assert session.latest_task is t2

    def test_latest_task_none_when_empty(self, make_session):
        session = make_session()
        assert session.latest_task is None

    def test_display_label_with_name(self, make_session):
        session = make_session()
        session.label_emoji = "\U0001f535"  # 🔵
        session.label_name = "blue"
        assert session.display_label == "\U0001f535 blue"

    def test_display_label_emoji_only(self, make_session):
        session = make_session()
        session.label_emoji = "\U0001f535"
        session.label_name = ""
        assert session.display_label == "\U0001f535"

    def test_consume_tools(self, make_session):
        session = make_session()
        session.next_tools = "Read,Write"
        result = session.consume_tools()
        assert result == "Read,Write"
        assert session.next_tools is None

    def test_consume_tools_none(self, make_session):
        session = make_session()
        assert session.consume_tools() is None
        assert session.next_tools is None

    def test_consume_tools_idempotent(self, make_session):
        session = make_session()
        session.next_tools = "Bash"
        session.consume_tools()
        assert session.consume_tools() is None


# ── Project ───────────────────────────────────────────────

class TestProject:
    def test_assign_label_first(self, make_project, make_session):
        project = make_project()
        session = make_session()
        project.assign_label(session)
        # 最初のラベルは TASK_LABELS[0]
        assert session.label_emoji == "\U0001f535"
        assert session.label_name == "blue"

    def test_assign_label_unique(self, make_project, make_session, make_task):
        project = make_project()
        # 1つ目のセッション: blue（アクティブタスク付き）
        s1 = make_session(thread_ts="ts1")
        project.assign_label(s1)
        t1 = make_task(id=1, status=TaskStatus.RUNNING)
        s1.tasks.append(t1)
        project.sessions["ts1"] = s1
        # 2つ目: blue は使用中なので green になる
        s2 = make_session(thread_ts="ts2")
        project.assign_label(s2)
        assert s2.label_name == "green"

    def test_assign_label_reuses_inactive(self, make_project, make_session, make_task):
        """アクティブタスクがないセッションのラベルは再利用される"""
        project = make_project()
        s1 = make_session(thread_ts="ts1")
        project.assign_label(s1)
        t1 = make_task(id=1, status=TaskStatus.COMPLETED)
        s1.tasks.append(t1)
        project.sessions["ts1"] = s1
        # s1 はアクティブタスクなし → blue が再利用可能
        s2 = make_session(thread_ts="ts2")
        project.assign_label(s2)
        assert s2.label_name == "blue"

    def test_assign_label_overflow(self, make_project, make_session, make_task):
        """TASK_LABELS を超えたらフォールバック"""
        project = make_project()
        for i, (emoji, name) in enumerate(bridge.TASK_LABELS):
            s = make_session(thread_ts=f"ts{i}")
            s.label_emoji = emoji
            s.label_name = name
            t = make_task(id=i + 1, status=TaskStatus.RUNNING)
            s.tasks.append(t)
            project.sessions[f"ts{i}"] = s
        # 次のセッションはフォールバック
        extra = make_session(thread_ts="extra")
        project.assign_label(extra)
        assert extra.label_emoji == "\u26aa"  # ⚪
        assert extra.label_name.startswith("session-")

    def test_get_or_create_session_new(self, make_project):
        project = make_project()
        session = project.get_or_create_session("ts_new")
        assert session.thread_ts == "ts_new"
        assert session.channel_id == project.channel_id
        assert "ts_new" in project.sessions

    def test_get_or_create_session_existing(self, make_project):
        project = make_project()
        s1 = project.get_or_create_session("ts1")
        s2 = project.get_or_create_session("ts1")
        assert s1 is s2

    def test_get_or_create_session_assigns_label(self, make_project):
        project = make_project()
        session = project.get_or_create_session("ts1")
        assert session.label_name  # ラベルが割り当てられている

    def test_find_task_by_id_found(self, make_project, make_session, make_task):
        project = make_project()
        session = make_session(thread_ts="ts1")
        task = make_task(id=42)
        session.tasks.append(task)
        project.sessions["ts1"] = session
        result = project.find_task_by_id(42)
        assert result is not None
        assert result == (session, task)

    def test_find_task_by_id_not_found(self, make_project):
        project = make_project()
        assert project.find_task_by_id(999) is None

    def test_find_session_by_claude_id(self, make_project, make_session):
        project = make_project()
        session = make_session(thread_ts="ts1")
        session.claude_session_id = "abc-def-123"
        project.sessions["ts1"] = session
        result = project.find_session_by_claude_id("abc-def-123")
        assert result is session

    def test_find_session_by_claude_id_partial(self, make_project, make_session):
        project = make_project()
        session = make_session(thread_ts="ts1")
        session.claude_session_id = "abc-def-123"
        project.sessions["ts1"] = session
        result = project.find_session_by_claude_id("abc-def")
        assert result is session

    def test_find_session_by_claude_id_not_found(self, make_project):
        project = make_project()
        assert project.find_session_by_claude_id("nonexistent") is None

    def test_active_sessions(self, make_project, make_session, make_task):
        project = make_project()
        s1 = make_session(thread_ts="ts1")
        s1.tasks.append(make_task(id=1, status=TaskStatus.RUNNING))
        s2 = make_session(thread_ts="ts2")
        s2.tasks.append(make_task(id=2, status=TaskStatus.COMPLETED))
        project.sessions["ts1"] = s1
        project.sessions["ts2"] = s2
        active = project.active_sessions
        assert len(active) == 1
        assert active[0] is s1

    def test_active_sessions_empty(self, make_project):
        project = make_project()
        assert project.active_sessions == []

    def test_all_tasks(self, make_project, make_session, make_task):
        project = make_project()
        s1 = make_session(thread_ts="ts1")
        s1.tasks.extend([make_task(id=1), make_task(id=2)])
        s2 = make_session(thread_ts="ts2")
        s2.tasks.append(make_task(id=3))
        project.sessions["ts1"] = s1
        project.sessions["ts2"] = s2
        all_tasks = project.all_tasks
        assert len(all_tasks) == 3
        ids = {t.id for t in all_tasks}
        assert ids == {1, 2, 3}

    def test_all_tasks_empty(self, make_project):
        project = make_project()
        assert project.all_tasks == []
