import csv
import io
from packtoworkeu.scanning.pipeline import _read_csv_window
from packtoworkeu.db import database as db


def test_partial_multiline_record_waits_for_closing_quote(tmp_path):
    p=tmp_path/'scan.csv'
    p.write_bytes(b'Company,Diagnostics,Duration Sec,Seed URL\r\nA,"first line\n')
    fields,text,offset=_read_csv_window(p,0)
    assert text==''
    with p.open('ab') as f:f.write(b'second ""quoted"" line",1.2,https://example.org\r\n')
    fields,text,end=_read_csv_window(p,offset)
    rows=list(csv.DictReader(io.StringIO(text),fieldnames=fields))
    assert len(rows)==1 and rows[0]['Duration Sec']=='1.2'
    assert rows[0]['Diagnostics']=='first line\nsecond "quoted" line'
    assert _read_csv_window(p,end)[1]==''


def test_invalid_duration_preserves_log_as_error(tmp_path):
    p=tmp_path/'db.sqlite';db.initialize(p)
    db.record_scan_log_rows(p,'run','career',[{'Company':'A','Duration Sec':'https://example.org'},{'Company':'B','Duration Sec':'2.5'}])
    with db.get_connection(p) as conn:
        rows=conn.execute('select company,status,duration_sec,error from scan_log order by company').fetchall()
    assert rows[0][0]=='A' and rows[0][1]=='error' and rows[0][2]==0
    assert 'https://example.org' in rows[0][3]
    assert rows[1][2]==2.5
