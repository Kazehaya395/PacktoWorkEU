"""Body-only internship evidence shared by ATS and Career classifiers."""
import re

_INTERNSHIP = re.compile(
    r'\bintern(?:ship|s)?\b|\bpraktikum\b|\bpraktikant\w*|\btirocinio\b'
    r'|\bapprendistato\b|\btrainee\b|\bapprentic\w*|\bstagiaire\b'
    r'|\bstage\s+(?:de|en|dans|chez)\b|\bausbildung\b|\bduales studium\b'
    r'|\bworking student\b|\bwerkstudent\w*|\bbecari\w*|\bpr[áa]cticas\b', re.I)
_DUTY = re.compile(r'\b(?:mentor|supervis|manag|coach|train|support|lead)\w*\b.{0,65}\b(?:interns?|internships?|trainees?|apprentices?)\b', re.I)
_ROLE = re.compile(r'\b(?:this (?:is an? )?internship|(?:role|position|opportunity)\s*(?:is|:).*intern|(?:seeking|hiring|looking for)\s+(?:an?\s+)?intern)\b', re.I)


def internship_role_evidence(text):
    # Titles and explicit structured employer fields retain their separate,
    # higher-priority treatment in the callers. Bare English "stage" is not
    # body evidence for a French internship.
    for sentence in re.split(r'(?<=[.!?;])\s+|[\n\r]+', text or ''):
        if _INTERNSHIP.search(sentence) and (not _DUTY.search(sentence) or _ROLE.search(sentence)):
            return True
    return False
