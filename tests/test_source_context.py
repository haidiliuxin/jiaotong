import tempfile
import unittest
from pathlib import Path

from traffic_agent.agent import Agent, plan
from traffic_agent.report import write_report
from traffic_agent.runner import DEFAULT_SOURCE
from traffic_agent.source_context import recommendation_scope, source_label
from traffic_agent.task import Task


class SourceContextTests(unittest.TestCase):
    def test_default_nanjing_claims_stay_compatible(self):
        task = Task()
        self.assertIn("非南京实测早高峰", plan(task)["assumptions"][0])
        self.assertEqual(plan(task), plan(task, DEFAULT_SOURCE))
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            agent = Agent(task, root=root)
            self.assertNotIn("source", agent.state)
            agent.job.mkdir(parents=True)
            agent.state.update(status="failed", error="测试停止")
            write_report(agent.job, agent.state)
            report = (agent.job / "report.md").read_text(encoding="utf-8")
            html = (agent.job / "report.html").read_text(encoding="utf-8")
            self.assertIn("赛事南京案例已做数据调整", report)
            self.assertIn("赛事调整案例 · 非实时交通", html)

    def test_other_dataset_has_accurate_plan_state_report_and_html(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "examples" / "教学案例4" / "基础路网"
            task = Task()
            planned = plan(task, source)
            self.assertNotIn("南京", planned["assumptions"][0])
            agent = Agent(task, source=source, root=root / "jobs")
            self.assertEqual(agent.state["source"], str(source.resolve()))
            self.assertEqual(recommendation_scope(source), "仅是所选输入案例中的仿真建议，非现实实施决定")
            agent.job.mkdir(parents=True)
            agent.state.update(status="failed", error="测试停止")
            write_report(agent.job, agent.state)
            report = (agent.job / "report.md").read_text(encoding="utf-8")
            html = (agent.job / "report.html").read_text(encoding="utf-8")
            self.assertIn("输入案例：软件自带教学案例4", report)
            self.assertIn("输入案例：软件自带教学案例4", html)
            self.assertNotIn("南京", report + html)

    def test_source_label_is_safe_for_report_markdown(self):
        source = Path("examples") / "<script>\n恶意" / "基础路网"
        self.assertNotIn("<", source_label(source))
        self.assertNotIn("\n", source_label(source))


if __name__ == "__main__":
    unittest.main()
