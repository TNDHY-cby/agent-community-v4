## 这个 PR 做了什么（一句话）

## 改前改后

- 改前：
- 改后：

## 我按项目的判据跑过（**贴原文输出，别写"通过"**）

```bash
python tools/check_portability.py       # fail=0
python tools/check_productization.py    # fail=0
python tools/gen_env_docs.py --check
python -m pytest -q
python tools/dual_platform_check.py     # ★双平台真跑（CI 替代判据）；退出码 2＝只跑成一侧＝不算通过
```

```
（把上面几条的真实输出贴这里）
```

## 对外表达是否一次全改（规则 20）

- [ ] 后端字段 / 界面话术 / 文档模板 / 验收脚本 / 示例 **一处不落**
- [ ] 若**只改了其中一处**，在这里说明为什么（不允许默默留着两份口径）

## 已知遗留 / 未做

（**空着＝没做，不要留空**；有遗留就如实写，本仓库最反对"看起来成功"。）
