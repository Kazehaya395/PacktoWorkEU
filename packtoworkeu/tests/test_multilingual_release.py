import pytest
from packtoworkeu.scanning.career.career_scanner import _static_accept_language, _static_next_page_url
from packtoworkeu.scanning.jd_support import JDSupportDetector

@pytest.mark.parametrize('lang,label,locale', [('de','Weiter','de-DE'),('it','Pagina successiva','it-IT'),('nl','Volgende','nl-NL'),('sv','Nästa','sv-SE'),('de','Mehr laden','de-DE'),('nl','Toon meer vacatures','nl-NL'),('sv','Visa fler jobb','sv-SE')])
def test_locale_and_forward_control(lang,label,locale):
    url=f'https://board.example/{lang}/jobs'
    assert _static_accept_language(url).split(',')[0]==locale
    def next_link(text):
        return _static_next_page_url(f'<a href="?page=2">{text}</a>',url)
    assert next_link(label)==url+'?page=2'
    assert not next_link(label+' Engineer')
    assert not next_link('Vorherige')

@pytest.mark.parametrize('text,verdict',[('Vi erbjuder visumsponsring.','Yes'),('Vi erbjuder inte visumsponsring.','No'),('Visa fler jobb','Unknown'),('Vi kan eventuellt erbjuda visumsponsring.','Unknown')])
def test_swedish_evidence(text,verdict):
    result=JDSupportDetector().detect(text)['visa']
    assert result['verdict']==verdict
    if verdict!='Unknown':
        assert result['evidence'][0][-1]==text

def test_ams_uuid_and_posting_fields():
    from packtoworkeu.scanning.career.career_scanner import CareerPortalScanner as S
    uuid='51194458-53e3-3cf5-99b2-22bc51f5c8e0'
    assert S._ams_uuid('https://jobs.ams.at/public/emps/jobs/'+uuid)==uuid
    assert not S._ams_uuid('https://elsewhere.example/public/emps/jobs/'+uuid)
    item=dict(uuid=uuid,objectType='searchDetailJobOffer',summary='Das ist zukünftig Ihr Job.\nAnalyse.',workingLocation=dict(freeText='Stuttgart, Stadtkreis',countryCode='DE'))
    assert S._ams_fields(uuid,item)['desc']==item['summary']
    assert S._ams_fields(uuid,item)['loc']=='Stuttgart, Stadtkreis, DE'
    assert not S._ams_fields(uuid,dict(item,uuid='wrong'))
    assert not S._ams_fields(uuid,{'message':'unauthorized'})
