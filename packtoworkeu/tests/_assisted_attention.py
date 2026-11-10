"""Drive simulated LOCAL human actions from the real worker attention contract.

Never use translated progress prose as a synchronization signal. Browser actions
remain on their owning worker; no production clearance/detection is mocked.
"""
def drive_attention(kwargs, pages, action):
    control=kwargs['control'];previous=control.on_attention
    def attention(info):
        if previous:previous(info)
        if info.get('waiting'):
            page=next(p for p in pages if not p.is_closed() and p.url==info['url'])
            action(info,page)
    control.on_attention=attention
