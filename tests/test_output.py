import unittest
import os
import time
from unittest.mock import patch
from datetime import datetime
from chrome_history_mcp.output import render_history_results, _visited


class OutputTests(unittest.TestCase):
    def page(self, title='Drone', url='https://example.com/drone', **extra):
        return dict(title=title, url=url, last_visited_at='2026-10-08T20:00:00+00:00', **extra)

    def render(self, pages, **extra):
        return render_history_results(dict(summary='Found matching pages.', pages=pages, notice='', **extra))

    def test_excerpts_stay_with_their_page(self):
        text = self.render([self.page(excerpt='First excerpt'), self.page('Second', 'https://example.com/second', excerpt='Second excerpt')])
        self.assertLess(text.index('https://example.com/drone'), text.index('First excerpt'))
        self.assertLess(text.index('First excerpt'), text.index('https://example.com/second'))
        self.assertLess(text.index('https://example.com/second'), text.index('Second excerpt'))
        expected = 'Oct 08, 2026 at 20:00 UTC'
        self.assertIn(expected, text)

    def test_content_is_escaped_and_url_encoded(self):
        text = self.render([self.page('[fake](evil)', 'https://example.com/a (b)', excerpt='<script>\n# heading\n[link](evil)')])
        self.assertIn('https://example.com/a%20%28b%29', text)
        self.assertNotIn('<script>', text)
        self.assertNotIn('\n# heading', text)
        self.assertIn(r'\[fake\]', text)

    def test_missing_excerpt_and_non_web_urls(self):
        text = self.render([self.page(url='javascript:alert(1)', match_note='Possible spelling match.')])
        self.assertNotIn('](<javascript:', text)
        self.assertNotIn('> ', text)
        self.assertIn('Possible spelling match', text)

    def test_empty_results_have_no_page_sections(self):
        text = self.render([])
        self.assertNotIn('###', text)
        self.assertNotIn('Visited:', text)


