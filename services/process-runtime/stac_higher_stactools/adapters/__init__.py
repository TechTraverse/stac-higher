"""One adapter per registry entry (spec §6): ``create(paths, draft)`` → a
``pystac.Item``. ``paths`` maps each draft asset key to the local file the
wrapper staged it at (original basename preserved); ``draft`` is the
manifest's item, whose asset hrefs are the only place the SOURCE path
survives (a SAFE tree, a bucket's region segment). Adapters import their
stactools package lazily so the wrapper's own import never depends on any
of them. Everything they return is merged by ``stac_higher_stactools.merge``
— an adapter never has to care about ids, hrefs or added assets.
"""
