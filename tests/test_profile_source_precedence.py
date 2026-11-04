# -*- coding: utf-8 -*-
"""个人画像到底以哪份文件为准。

面板「个人画像」那一页保存时只写 user_profile.json（flask-version/app.py:1457），
但加载顺序是 bot_config.json → user_profile.json → config_overrides.json，
而 config_overrides.json 里也存了一份 user_profile 快照（它是保存整份配置时
同步进去的）。快照后应用，于是画像页上改的字看不见效果——
2026-10-11 三端一致性测试就是这么炸出 9 条不一致的：
user_profile.json 写着 黄维 / 本科在读（统计学）/ 有数据标注经验，
生效值却是 求职者 / 本科学历 / 空经验。
经验被抹成空，AI 回「你有经验吗」时手上就没有可引用的东西，答非所问。
"""
import json

from boss_bot.unified_config import UnifiedConfig


def _加载(tmp_path, 画像, 快照):
    (tmp_path / "user_profile.json").write_text(
        json.dumps(画像, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "config_overrides.json").write_text(
        json.dumps({"user_profile": 快照}, ensure_ascii=False), encoding="utf-8")
    return UnifiedConfig.load(
        config_path=str(tmp_path / "bot_config.json"),
        profile_path=str(tmp_path / "user_profile.json"),
        overrides_path=str(tmp_path / "config_overrides.json"),
    )


class 画像页保存要真生效Test:
    def test_画像文件赢过覆盖文件里的旧快照(self, tmp_path):
        cfg = _加载(tmp_path,
                    {"name": "黄维", "experience": "有数据标注经验", "education": "本科在读（统计学）"},
                    {"name": "求职者", "experience": "", "education": "本科学历"})
        assert cfg.user_profile.name == "黄维"
        assert cfg.user_profile.experience == "有数据标注经验"
        assert cfg.user_profile.education == "本科在读（统计学）"

    def test_快照仍然兜住画像文件没写的字段(self, tmp_path):
        """画像页不管的字段（历史遗留写在快照里）不能因为这次改动直接变空。"""
        cfg = _加载(tmp_path, {"name": "黄维"},
                    {"name": "求职者", "highlights": ["上手就能干"], "contact": "13800000000"})
        assert cfg.user_profile.name == "黄维"
        assert cfg.user_profile.highlights == ["上手就能干"]
        assert cfg.user_profile.contact == "13800000000"

    def test_空字符串不许把有值的画像抹掉(self, tmp_path):
        cfg = _加载(tmp_path, {"skills": ["数据标注", "SQL"], "salary_expectation": "面议"},
                    {"skills": [], "salary_expectation": ""})
        assert cfg.user_profile.skills == ["数据标注", "SQL"]
        assert cfg.user_profile.salary_expectation == "面议"

    def test_真机上那三份要对得上(self):
        """仓库里现在这三份文件必须给出同一个名字，不然面板和存档又是两套。"""
        from pathlib import Path
        根 = Path(__file__).resolve().parent.parent
        cfg = UnifiedConfig.load(
            config_path=str(根 / "bot_config.json"),
            profile_path=str(根 / "user_profile.json"),
            overrides_path=str(根 / "config_overrides.json"),
        )
        画像文件 = json.loads((根 / "user_profile.json").read_text(encoding="utf-8"))
        assert cfg.user_profile.name == 画像文件["name"], \
            "画像页保存的名字没生效：检查 config_overrides.json 里的 user_profile 快照"


def _真机三份():
    from pathlib import Path
    根 = Path(__file__).resolve().parent.parent
    return UnifiedConfig.load(
        config_path=str(根 / "bot_config.json"),
        profile_path=str(根 / "user_profile.json"),
        overrides_path=str(根 / "config_overrides.json"),
    )


class 方向只剩两条腿Test:
    """2026-11-02 他的原话：「只保留纯粹的一个是数据分析，一个是AI产品或者AI运营」。

    这条口径第一次改的时候我只动了 bot_config.json 和 config_overrides.json，
    面板 `/api/config` 回读出来 position 还是「AI产品经理 / 数据分析师」——
    因为加载顺序里 user_profile.json 排在 bot_config 之后、无条件覆盖（上面
    `_apply_user_profile`），而 overrides 对画像只补空。改错一层等于没改。
    所以这里判的是**生效值**，不是哪份文件里写了什么。
    """

    def test_生效画像要同时写着这两个方向(self):
        位 = _真机三份().user_profile.position
        assert "AI运营" in 位 or "AI 运营" in 位, \
            "生效画像里丢了 AI运营：八成是只改了 bot_config，user_profile.json 把它盖回去了"
        assert "数据分析" in 位

    def test_两号的方向词都不许再出现标注(self):
        cfg = _真机三份()
        for 号 in range(len(cfg.greet.accounts)):
            生效 = cfg.apply_account(号)
            词 = list(生效.ai.target_job_keywords or [])
            assert 词, f"账号{号} 方向词空了，正向闸会形同虚设"
            脏 = [w for w in 词 if "标注" in w or "质检" in w]
            assert not 脏, f"账号{号} 还在把标注类当目标方向：{脏}"

    def test_号0要能收到运营岗号1只要数据(self):
        cfg = _真机三份()
        零 = list(cfg.apply_account(0).ai.target_job_keywords or [])
        assert any("运营" in w for w in 零), "号0 的方向词没有运营，AI运营岗进不了候选"
        壹 = list(cfg.apply_account(1).ai.target_job_keywords or [])
        assert all("产品" not in w for w in 壹), "号1 混进了产品方向，和他定的两条腿串台"

    def test_号0的搜索任务里真有一条运营(self):
        """方向词只拦不投的岗；要把运营岗捞进池子，得真有一条搜索 query 去抓。"""
        cfg = _真机三份()
        任务 = [j for j in cfg.greet.accounts[0].jobs if getattr(j, "enabled", True)]
        assert any("运营" in (getattr(j, "query", "") or "") for j in 任务), \
            "号0 没有写「运营」的搜索任务，AI运营岗位根本不会被抓进来"

    def test_生效画像的技能与亮点不含标注(self):
        画 = _真机三份().user_profile
        for 项 in list(画.skills) + list(画.highlights):
            assert "标注" not in 项 and "质检" not in 项, f"画像还挂着标注类技能：{项}"


class 规则话术要跟着画像走Test:
    """薪资与「岗位做什么」这两句以前是写死在配置里的成品句。

    11-02 现读线上配置：`reply_templates.SALARY_REPLY` 写死「我的期望薪资是 面议」，
    而画像里明明填着 8-12K、两份在线简历一个写 7-8K 一个写 11-12K —— HR 问一句
    「期望多少」，机器答的和他简历上印的对不上。`JOB_CONTENT_REPLY` 更糟：
    「我了解这个岗位主要负责数据分析相关工作，我，相信能快速上手」这句话两个号都在用，
    投 AI 产品的号也照着说"数据分析"。
    判据打在**生效值**上：`config_overrides.json` 的 reply_templates/reply_rules 是
    保存整份配置时同步过去的快照，加载顺序排在 bot_config 之后（`_apply_overrides`），
    只改 bot_config 等于没改。
    """

    def test_薪资句要念画像里那个数不许写死面议(self):
        cfg = _真机三份()
        薪 = cfg.user_profile.salary_expectation
        assert 薪 and 薪 != "面议", f"画像的期望薪资本身是空的/面议：{薪!r}"
        assert 薪 in cfg.templates.salary_reply, \
            "薪资话术没引用画像里的数，八成是 config_overrides.json 的快照又写死了一句"
        for 键, 值 in cfg.rules.reply_rules.items():
            if isinstance(值, str):
                assert "面议" not in 值, f"规则「{键}」还在替他说「面议」"

    def test_职责句不许把某个方向焊死(self):
        cfg = _真机三份()
        句 = cfg.templates.job_content_reply
        assert "数据分析" not in 句, f"两个号共用这句，写死方向就是串台：{句}"
        assert "我，相信" not in 句, "病句（逗号加半句）又回来了"

    def test_标注在经历里只算干过的活儿不算方向(self):
        """标注不是求职方向，可他真做过——画像要把这句留着并写明"不是这次找的方向"。

        摘干净会有两个下场：① HR 问「有标注经验吗」时 AI 手上没依据，答出来就是
        装没做过（口径见 memory「问经验就答有经验」）；② 打招呼腿的 ask_experience
        成品话术还在说做过标注，画像却否认，两头对不上。
        方向那一头由 position / 技能 / 亮点 / 两号词表四格管着，那里才许一刀切。
        """
        cfg = _真机三份()
        经验 = cfg.user_profile.experience
        assert "标注" in 经验, \
            "画像经历把标注整句删了：AI 答「有没有标注经验」时没依据，只能装没做过"
        assert "不是这次找的方向" in 经验, \
            "标注要写成干过的活儿，不许读成还在找这个方向：" + 经验[-40:]
        assert "标注" not in cfg.user_profile.position, "求职意向又挂上标注了"
        assert "标注" in cfg.rules.reply_rules, \
            "规则里那条接话也得留着，双保险：HR 直接问标注时不靠模型即兴"
        答 = cfg.rules.reply_rules["标注"]
        assert "做过" in 答, f"接话那句要认事实，不许含糊：{答}"

    def test_三份文件的经历是同一句(self):
        """画像、bot_config、config_overrides 各存一份 experience。

        面板保存配置时会重写其中几份（bot_config 甚至会在跑动中被写），
        只改一份就会出现"生效的是另一份"这种查不出来的串台。
        """
        from pathlib import Path
        根 = Path(__file__).resolve().parent.parent
        份 = []
        for 名 in ("user_profile.json", "bot_config.json", "config_overrides.json"):
            对 = json.loads((根 / 名).read_text(encoding="utf-8"))
            经 = (名 == "user_profile.json" and 对.get("experience")) or \
                ((对.get("user_profile") or {}).get("experience")) or ""
            assert 经, "%s 没带上 experience 这句" % 名
            份.append(经)
        assert len(set(份)) == 1, "三份文件的 experience 不是同一句：改一处不够"

    def test_画像里的学历档次要真能让招呼语过闸(self):
        """11-04 改专业那一下把「双一流」从 education 里抹掉过一次，查不出来。

        闸门只认画像：画像里没有的档次不许机器替他报（greet_engine 里那段手写招呼语
        过闸就是这条）。education 少这三个字，那条来源的招呼语就整条回空，
        日志上只留一句"话术不可用"——所以这里既判这句话在，也判它真管用。
        """
        from boss_bot.reply_engine import reply_rejection

        句 = "我是双一流的本科"
        对 = _真机三份().user_profile
        学历 = 对.education or ""
        assert "双一流" in 学历, "画像 education 丢了「双一流」，那条手写招呼语会被闸门整条拦回"
        assert "风景园林" in 学历, "他 11-04 定的是风景园林：" + 学历
        assert "统计学" not in 学历, "专业又退回统计学了：" + 学历
        assert reply_rejection(句, profile={"education": 学历}) == "", \
            "画像带着双一流，这句话却还过不了闸——判据和画像对不上了"
        # 反向哨兵：把双一流拿掉，同一条门必须真的会拦（不然上面那条绿是空的）
        assert reply_rejection(句, profile={"education": 学历.replace("，双一流", "")}), \
            "拿掉双一流居然还放行，说明这条门根本不看画像"
