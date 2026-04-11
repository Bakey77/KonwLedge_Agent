# annual_report_generator.py
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# 注册中文字体（macOS）
pdfmetrics.registerFont(TTFont('ArialUnicode', '/Library/Fonts/Arial Unicode.ttf'))

def create_annual_report():
    c = canvas.Canvas("年度报告.pdf", pagesize=A4)
    width, height = A4

    # 封面
    c.setFont("ArialUnicode", 28)
    c.drawCentredString(width/2, height - 5*cm, "AgentKnowledgeHub")
    c.setFont("ArialUnicode", 18)
    c.drawCentredString(width/2, height - 6*cm, "2024年度报告")
    c.setFont("ArialUnicode", 12)
    c.drawCentredString(width/2, height - 8*cm, "企业级多Agent知识管理系统")
    c.showPage()

    # 第二页：公司概况
    c.setFont("ArialUnicode", 20)
    c.drawString(3*cm, height - 3*cm, "一、公司概况")
    c.setFont("ArialUnicode", 12)
    text = [
        "AgentKnowledgeHub 是腾讯公司打造的企业级多Agent知识管理系统。",
        "系统采用4个AI Agent分工协作，完成企业知识的全生命周期管理：",
        "  • DocParserAgent - 文档解析Agent",
        "  • KnowledgeExtractAgent - 知识抽取Agent",
        "  • QAAgent - 问答Agent",
        "  • KnowledgeUpdateAgent - 知识更新Agent",
        "",
        "核心技术亮点：",
        "  • 多模态RAG - 支持PDF、图片、表格等多种格式",
        "  • GraphRAG - 融合向量检索和知识图谱检索",
        "  • CDC增量更新 - 文档变更只更新变化部分"
    ]
    y = height - 5*cm
    for line in text:
        c.drawString(3*cm, y, line)
        y -= 0.6*cm

    # 第三页：团队成员
    c.showPage()
    c.setFont("ArialUnicode", 20)
    c.drawString(3*cm, height - 3*cm, "二、核心团队")
    c.setFont("ArialUnicode", 12)

    members = [
        ("刘汝蔚", "产品负责人", "负责微信相关业务", "清华大学"),
        ("王五", "产品经理", "负责产品规划与设计", "北京大学"),
        ("小白", "技术负责人", "负责电商平台架构", "浙江大学"),
        ("李明", "算法工程师", "负责知识图谱构建", "上海交通大学"),
        ("张晓", "后端开发", "负责LangGraph编排引擎", "复旦大学"),
        ("陈静", "前端开发", "负责Web界面开发", "南京大学"),
        ("周强", "测试工程师", "负责质量保障", "中国科技大学"),
        ("吴芳", "运维工程师", "负责Docker容器化部署", "哈尔滨工业大学"),
    ]

    y = height - 5*cm
    for name, role, resp, school in members:
        c.setFont("ArialUnicode", 11)
        c.drawString(3*cm, y, f"{name} - {role}")
        c.setFont("ArialUnicode", 10)
        c.drawString(4*cm, y - 0.4*cm, f"职责: {resp} | 毕业院校: {school}")
        y -= 1.2*cm

    c.save()
    print("年度报告.pdf 已生成！")

create_annual_report()