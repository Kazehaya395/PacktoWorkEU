"""Preserve bounded context while avoiding redundant browser round trips."""
import pytest
from packtoworkeu.core import blocked_portals as bp
from packtoworkeu.tests.test_career_assisted_harvest_2026_10_29 import local_chromium


def test_individual_card_text_uses_one_browser_read():
    class Anchor:
        calls=0
        def evaluate(self,script,urls):
            self.calls+=1
            assert 'found.length > 1'in script
            assert 'admitted.has(a.href)'in script
            return 'Backend Engineer\nBerlin, Germany\nRequirements: Python.'
        def evaluate_handle(self,*a):pytest.fail('unnecessary remote DOM handle')
    anchor=Anchor()
    assert 'Berlin'in bp._bounded_card_text(anchor,['https://fixture.invalid/jobs/1'])
    assert anchor.calls==1


@pytest.mark.parametrize('content,admitted,expected',[
    ('<article><a href="https://fixture.invalid/jobs/1">Backend Engineer</a><p>Berlin, Germany. Requirements: Python.</p></article>', ['https://fixture.invalid/jobs/1'],'Berlin'),
    ('<article><a href="https://fixture.invalid/jobs/1">Backend Engineer</a><p>Berlin, Germany.</p><a href="https://fixture.invalid/jobs/2">Data Engineer</a><p>Paris, France.</p></article>',['https://fixture.invalid/jobs/1','https://fixture.invalid/jobs/2'],''),
    ('<article><a href="https://fixture.invalid/jobs/1">Backend Engineer</a><p>Berlin, Germany.</p><a href="https://fixture.invalid/posizioni-aperte/2">Data Engineer</a><p>Paris, France.</p></article>',['https://fixture.invalid/jobs/1','https://fixture.invalid/posizioni-aperte/2'],''),
    ('<article><a href="https://fixture.invalid/jobs/1">Backend Engineer</a><p>'+('x'*4001)+'</p></article>',['https://fixture.invalid/jobs/1'],''),
])
def test_single_browser_read_preserves_context_boundaries(local_chromium,content,admitted,expected):
    with local_chromium.new_context()as context:
        page=context.new_page();page.set_content(content)
        text=bp._bounded_card_text(page.query_selector('a'),admitted)
        if expected:assert expected in text
        else:assert text==''
