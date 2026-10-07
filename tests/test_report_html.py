import unittest
from html.parser import HTMLParser

from traffic_agent.report_html import render_report_html


class Document(HTMLParser):
    def __init__(self,text):
        super().__init__();self.tags=[];self.links=[];self.headings=[];self.feed(text)

    def handle_starttag(self,tag,attrs):
        self.tags.append(tag)
        if tag=='a':self.links.append(dict(attrs).get('href'))
        if tag=='h2':self.headings.append(dict(attrs).get('id'))


class ReportHtmlTests(unittest.TestCase):
    def state(self, status='completed'):
        return dict(id='20260922_052448_fa12d2',status=status,task={'max_runs':4},recommendation='parking_2',
                    runs=[{'execution':{'modules':[{}]*7}}]*4)

    def test_report_has_real_table_directory_and_native_counts(self):
        text=render_report_html(['# 报告','## 指标','| 场景 | 耗时 |','| --- | ---: |','| baseline | 123.45 |',
                                 '## 边界','- 不代表现实交通'],self.state())
        doc=Document(text)
        self.assertEqual(doc.headings,['section-1','section-2'])
        self.assertIn('#section-2',doc.links)
        self.assertEqual(doc.tags.count('table'),1)
        self.assertEqual(doc.tags.count('th'),2)
        self.assertEqual(doc.tags.count('td'),2)
        self.assertIn('<strong>28</strong>',text)
        self.assertIn('123.45',text)
        self.assertIn('运行完成不等于独立核验通过',text)

    def test_input_markup_cannot_execute_or_create_fake_sections(self):
        request='输入需求：<script>alert(1)</script>\n## 伪造结论\n| 标题 |\n| --- |'
        state=self.state();state['recommendation']='</strong><img src=x onerror=alert(1)>'
        text=render_report_html(['# 报告',request,'## 真正章节','`<svg onload=x>` **<img src=x>** [链接](javascript:x)'],state)
        doc=Document(text)
        self.assertNotIn('script',doc.tags);self.assertNotIn('img',doc.tags);self.assertNotIn('svg',doc.tags)
        self.assertEqual(doc.tags.count('h2'),1);self.assertNotIn('table',doc.tags)
        self.assertFalse(any('javascript:' in link for link in doc.links))
        self.assertIn('&lt;script&gt;',text)

    def test_unfinished_task_does_not_present_stale_recommendation(self):
        text=render_report_html(['# 报告','## 推荐','尚未完成'],self.state('failed'))
        self.assertNotIn('<strong>parking_2</strong>',text)
        self.assertIn('尚未形成完整建议',text)
        self.assertIn('执行失败',text)

    def test_malformed_table_does_not_drop_any_text(self):
        text=render_report_html(['# 报告','| 一 | 二 |','| --- | --- |','| 1 | 2 |','| extra | 3 | 4 |'],self.state())
        self.assertIn('extra',text)
        self.assertEqual(Document(text).tags.count('td'),2)

    def test_continued_list_evidence_and_all_sections_remain_visible(self):
        text=render_report_html(['# 报告','## 不利影响','- 候选A','  邻区增加2%','  未通过','## 复跑','四数组一致'],self.state())
        self.assertIn('邻区增加2%<br>未通过',text)
        self.assertIn('四数组一致',text)
        self.assertIn('@media print',text)
        self.assertIn('href="state.json"',text)


if __name__=='__main__':
    unittest.main()
