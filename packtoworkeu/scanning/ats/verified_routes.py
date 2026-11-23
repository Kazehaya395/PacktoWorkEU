"""Narrow compatibility for obsolete shipped adapter triples. Never match by name alone."""
ROUTES = {
 ('https://job-boards.eu.greenhouse.io/brainrocketltd','bamboohr','brainrocket'):('greenhouse','brainrocketltd'),
 ('https://jobs.virtuagym.com/jobs','recruitee','virtuagym'):('teamtailor','virtuagym'),
 ('https://jobs.ashbyhq.com/applike-group','ashby','applike'):('ashby','applike-group'),
 ('https://raisin.jobs.personio.de/?language=de','personio','raisin'):('greenhouse','raisin'),
 ('https://itonics-gmbh.jobs.personio.de/?language=en','personio','itonics'):('personio','itonics-gmbh'),
 ('https://recruiting2.ultipro.com/QUA1014QUAM/JobBoard/ca0ab154-0d7f-47e3-af84-cd9bd79768dc/?q=&o=postedDateDesc&w=&wc=&we=&wpst=','personio','quantum-diamond'):('ashby','quantumdiamonds'),
}

def verified_target(target):
    # The configured Personio tenant is ITONICS, not the separately named
    # IT-Tronics GmbH. Never turn a repaired slug into cross-employer data.
    if (target.get('name','').casefold().startswith('it-tronics') and
            target.get('url')=='https://itonics-gmbh.jobs.personio.de/?language=en'):
        raise ValueError('Seed identity conflict: IT-Tronics is named but the configured board belongs to ITONICS. Correct the employer name or supply its own careers URL in Data Management; no postings imported.')
    fix=ROUTES.get((target.get('url'),target.get('ats_type'),target.get('board_slug')))
    return dict(target,ats_type=fix[0],board_slug=fix[1]) if fix else target
