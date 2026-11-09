"""The parent hook collects the driver with the browser-cache filter.

Override the SDK entry-point hook, which otherwise collects all package data
again and would reintroduce a hermetic .local-browsers directory.
"""
datas = []
