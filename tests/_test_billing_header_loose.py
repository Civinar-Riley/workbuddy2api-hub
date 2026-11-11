#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""计费头的宽松兜底（`WB_SANITIZE_LOOSE_BILLING`，默认关闭）。

背景：#853 / #854 把清洗作用域收紧成「只认 system/developer 块起始、且整块都是
`cc_*` / `cch` 元数据」的形态，误删风险最低。但只要客户端改了字段名、多塞一个未知
键，`_billing_payload_is_metadata()` 就判它「不是元数据」，整行原样留下——而这类行
**每个请求都不同**，留在 system 首部等于每一轮都把整段前缀的 prompt 缓存打掉，命中率
静默掉到 0，没有任何报错可循。

这个兜底就是给那种情况用的：严格解析认不出、但首行看起来确实是计费头时，整段删掉。
默认**关闭**（与 #853 之后的行为逐字节一致），`WB_SANITIZE_LOOSE_BILLING=1` 才生效。

本套件钉四件事：
  1. 默认关闭：认不出的行原样返回，行为与现在一致；
  2. 打开后：认不出的计费头连同它的续行整段删除，后续正文保留；
  3. 打开后也不误删：普通 system 首行、超长行、只有单个键值对的散文都不动；
  4. 能走严格路径的形态，两种模式下结果完全相同（兜底不改变已有判定）。

运行：python3 tests/_test_billing_header_loose.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import wb_proxy

CLEAN = "x-anthropic-billing-header: cc_version=2.0.1; cch=deadbeef\n"
#: 清洗器返回的是 `"".join(lines[end:]).strip()`，尾部空白会被吃掉，期望值取无尾换行版。
BODY = ("You are Claude Code, Anthropic's official CLI for Claude.\n"
        "You answer questions about the repository in the current directory.")
STRICT_BLOCK = BODY + "\n"


class _Base(unittest.TestCase):
    def setUp(self):
        self._old = wb_proxy.SANITIZE_LOOSE_BILLING

    def tearDown(self):
        wb_proxy.SANITIZE_LOOSE_BILLING = self._old

    def loose(self, text):
        wb_proxy.SANITIZE_LOOSE_BILLING = True
        return wb_proxy.sanitize_billing_metadata_prefix(text)

    def strict(self, text):
        wb_proxy.SANITIZE_LOOSE_BILLING = False
        return wb_proxy.sanitize_billing_metadata_prefix(text)


class TestSwitchIsOffByDefault(unittest.TestCase):
    def test_default_is_off(self):
        self.assertFalse(wb_proxy.SANITIZE_LOOSE_BILLING)

    def test_env_parsing(self):
        for raw in ("1", "true", "YES", "On"):
            self.assertIn(raw.strip().lower(), ("1", "true", "yes", "on"))
        # 显式的空/0 都是关
        for raw in ("", "0", "false", "no", "off"):
            self.assertFalse(raw.strip().lower() in ("1", "true", "yes", "on"))


class TestStrictPathUnchanged(_Base):
    def test_well_formed_header_is_stripped_in_both_modes(self):
        text = CLEAN + STRICT_BLOCK
        self.assertEqual(self.strict(text), BODY)
        self.assertEqual(self.loose(text), BODY)

    def test_continuation_lines_are_stripped_in_both_modes(self):
        text = ("x-anthropic-billing-header: cc_version=2.0.1\n"
                "cch=abc123\n"
                + BODY)
        self.assertEqual(self.strict(text), BODY)
        self.assertEqual(self.loose(text), BODY)

    def test_clean_text_is_untouched(self):
        self.assertEqual(self.strict(BODY), BODY)
        self.assertEqual(self.loose(BODY), BODY)


class TestLooseFallback(_Base):
    def test_unknown_key_in_header_is_stripped_when_loose(self):
        text = ("x-anthropic-billing-header: cc_version=2.0.1; cch=abc; quota_hint=A\n"
                + BODY)
        self.assertEqual(self.strict(text), text, "默认行为：认不出就原样留着")
        self.assertEqual(self.loose(text), BODY)

    def test_leading_prose_in_payload_is_stripped_when_loose(self):
        text = ("x-anthropic-billing-header: v2 wei; cch=nope\n" + BODY)
        self.assertEqual(self.strict(text), text)
        self.assertEqual(self.loose(text), BODY)

    def test_continuation_lines_follow_the_loose_block(self):
        """续行只在它自己也是合法元数据行时才被带走。

        `cc_version=2.0.1; extra=x` 里的 `extra=x` 不是 `cc_*` / `cch` 键，既不被
        `SANITIZE_BILLING_CONT_RE` 认可、也不够两个键值对，所以宽松块在这一行停下
        ——宁可少删一行，也不要顺着未知键继续往下吞正文。
        """
        text = ("x-anthropic-billing-header: weird_shape\n"
                "cch=abc123\n"
                "cc_version=2.0.1; extra=x\n"
                + BODY)
        self.assertEqual(self.strict(text), text)
        self.assertEqual(self.loose(text), "cc_version=2.0.1; extra=x\n" + BODY)

    def test_loose_stops_at_first_non_header_line(self):
        text = ("x-anthropic-billing-header: weird_shape\n"
                + BODY)
        self.assertEqual(self.loose(text), BODY)

    def test_lowercase_variant_is_recognised(self):
        text = ("x-anthropic-billing-HDR: odd_payload here\n" + BODY)
        self.assertEqual(self.strict(text), text)
        self.assertEqual(self.loose(text), BODY)


class TestLooseDoesNotOverreach(_Base):
    def test_prose_first_line_is_kept(self):
        text = ("Please explain how the billing header works.\n" + BODY)
        self.assertEqual(self.loose(text), text)

    def test_single_kv_token_is_kept(self):
        text = ("Configuration note: cch=abc123 appears in logs.\n" + BODY)
        self.assertEqual(self.loose(text), text,
                         "只有一个键值对的散文不该被当成计费头")

    def test_overlong_line_is_kept(self):
        # 400 字符以上一律不认：这种行更像正文里的表格/日志片段，不是头部元数据
        text = ("x-anthropic-billing-header: " + "prose without pairs " * 40
                + "\n" + BODY)
        self.assertEqual(self.loose(text), text, "超长行一律不动")

    def test_two_kv_tokens_mid_prose_is_the_known_edge(self):
        """已知边界：≥2 个键值对的散文行会被宽松判定命中——所以默认关闭。"""
        text = ("Deploy notes: cch=abc; cc_version=2.0.1 in the request.\n" + BODY)
        self.assertNotEqual(self.loose(text), text)
        self.assertEqual(self.strict(text), text)


class TestEmptyInputs(_Base):
    def test_empty_and_non_string(self):
        for value in ("", None, 0, b"x"):
            self.assertEqual(wb_proxy.sanitize_billing_metadata_prefix(value), value)


if __name__ == "__main__":
    unittest.main(verbosity=2)
