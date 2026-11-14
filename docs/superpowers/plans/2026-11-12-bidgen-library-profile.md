# 投标资料库自动填充实施计划

> 执行方式：在当前工作区内完成修改与验证，保留已有未提交实现。

**目标：** AI 标书生成的四个资料页签读取左侧“投标资料库”页面使用的同一知识库记录、识别字段、正文与附件；没有可靠匹配的内容由用户补充。

**范围与约束：** 仅改 bidgen 模块和对应前端组件；不修改受保护模块，不新建另一份资料来源，不调用外部模型，不更改数据库结构。沿用 Vue / Python 命名惯例。数值保持原始精度，日期取资料内容。用户手工更正保留，多公司或字段冲突提示核对。新资料不使用演示数据；仅完整、未更改的旧演示档案可自动迁移为空档案。

**实现选择：** 后端直接读取投标资料库相同的 KnowledgeBase / KnowledgeDocument 与本地提取正文，在 bidgen 内做字段映射并保存使用快照。前端调用统一 profile 接口展示匹配状态和缺项，不自行解析、拼凑资料库接口。

- [x] 测试先覆盖：空库、四页字段和表格、证件原件和有效期、重复读取、人工更正、不同企业和冲突、被拒绝资料、旧演示档案迁移。
- [x] 新增 bidgen/library.py 和 library_fields.py，读取实际资料库，基于确定字段与表头匹配，保持来源信息。
- [x] ProfileStore 支持按来源合并与附件快照；GET /bid-gen/profile 自动匹配，权限和失败提示沿用项目约定。
- [x] 前端显示匹配来源、缺项补填提示与重新匹配入口；读取失败时能重试。
- [x] 定向 pytest、组件行为检查、前端打包、保护基线与 import 检查；真实资料库读取结果为空，数据库及 HTTP 接口联通通过隔离集成测试验证。

**检查重点：** 多家企业资料不能混用；公司电话不取身份证持有人的电话；列表合并不重复；资料库原件不可被表单删除；已手填内容不能在重新匹配时覆盖。

**基线：** 原保护脚本使用 Windows 外部前端路径，在本机原样执行报缺失；将 EXTERNAL_FILES 在内存指向实际前端路径后，60 个文件与既有基线一致。本机资料库尚未创建，使用隔离样例验证有资料场景。

## 追加需求与验证结果

用户随后要求删除技术标、商务标正文中“评审要素／评审细则”段落。已删除 business_sections.py 的公共输出函数及两个生成模块内全部调用；评分关联仍用于自查与已有可选索引。对商务标、技术标、完整版实际生成 DOCX，读取 Word XML 确认目标段落消失且响应正文存在。

- 后端：`pytest tests/test_bidgen_library.py tests/test_bidgen_scoring_copy.py tests/test_document_expiry.py tests/test_tender_facts.py tests/test_kb_access.py -q`，89 通过。
- 前端：`node tests/bid-gen-profile.test.cjs` 通过；`vue-tsc --noEmit -p tsconfig.app.json --noUnusedLocals false --noUnusedParameters false` 与 `vite build` 通过。
- 原 `npm run build` 在受保护的 TendersView.vue 有 10 个既有 TS6133 未使用声明错误，此次未修改该文件或关闭项目规则。
- 独立审查发现的人员混填、人工清空恢复、证件占位 ID 复用问题均添加失败用例后修复。
- 60 个受保护文件与基线一致；cablepkg import 静态检查无违规。
- 未完成运行中服务的浏览器联调：CUA 连接两次超时；只读进程/本机 HTTP 检查被自动审批服务因额度耗尽拒绝执行，未绕过审批，也未重启用户进程。
