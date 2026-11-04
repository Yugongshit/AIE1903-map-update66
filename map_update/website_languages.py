"""Build-time website localization; raw annotations and evaluation inputs stay intact."""
import json
from pathlib import Path
import re

PACKAGE = Path(__file__).resolve().parent


def catalog():
    entries = {}
    for line in (PACKAGE / 'website_en.tsv').read_text().splitlines():
        if not line:
            continue
        source, english = line.split('\t', 1)
        english = english.replace(r'\s', ' ')  # Explicit boundary spaces in TSV.
        if source in entries:
            raise ValueError('Duplicate translation: ' + source)
        # These values are inserted into existing JS string literals and HTML.
        if any(c in english for c in "\"'`<>\\"):
            raise ValueError('Unsafe inline translation: ' + source)
        entries[source] = english
    return entries


ENGLISH = catalog()
PATTERN = re.compile('|'.join(re.escape(s) for s in sorted(ENGLISH, key=len, reverse=True)))
PUNCTUATION = str.maketrans({'；': '; ', '：': ': ', '（': '(', '）': ')', '，': ', ', '。': '.', '、': ', '})


def english_text(text):
    return PATTERN.sub(lambda match: ENGLISH[match.group()], text).translate(PUNCTUATION)


def localized_issues(issues, language):
    if language == 'zh':
        return issues
    result = []
    for issue in issues:
        item = {k: english_text(v) for k, v in issue.items()}
        # File names are provenance, not translated file-system addresses.
        item['source_reference_original'] = issue['source']
        item['source'] = issue['source'].replace('AIE1903 原始资料/', 'Source bundle / ')
        if issue['id'] == 'P05':
            item['source'] = 'Current integration interface; docs/m3_integration_and_architecture.md'
        if issue['id'] == 'R02':
            item['source'] = 'Map-maintenance presentation, slides 2–3; map_update/engine.py'
        item['source'] = item['source'].replace('；', '; ')
        result.append(item)
    return result


def localize_html(document, language, review=False):
    if language not in ('en', 'zh'):
        raise ValueError('Expected en or zh')
    # Protect the data block from source-code translation.
    pattern = re.compile(r'(<script[^>]*type="application/json"[^>]*>)(.*?)(</script>)', re.S)
    match = pattern.search(document)
    if not match:
        raise ValueError('Missing source data block')
    payload = json.loads(match[2])
    document = document[:match.start(2)] + '__LOCALIZED_DATA__' + document[match.end(2):]
    if not review:
        payload['issues'] = localized_issues(payload['issues'], language)
        if payload.get('perception_video'):
            payload['perception_video']['path'] = '../assets/scene0980-perception.mp4'
    if language == 'en':
        document = english_text(document).replace('lang="zh-CN"', 'lang="en"')
        if not review:
            # The English display uses exact source category values, not a
            # reverse translation of the Chinese display names.
            document = document.replace('a.name_zh', 'a.category')
            document = document.replace("180,Math.ceil", "330,Math.ceil").replace("200,Math.ceil", "330,Math.ceil")
            document = document.replace("measure.font=(width<500?'10':'11')+'px sans-serif'", "measure.font='10px monospace'")
            document = document.replace('n.textContent=text;', 'n.textContent=uiText(text);')
            document = document.replace('assumptions:LAB.assumptions,', 'assumptions:LAB.assumptions.map(uiText),')
        else:
            document = document.replace('p.textContent=copy;', 'p.textContent=uiText(copy);')
    serialized = json.dumps(payload, ensure_ascii=False, allow_nan=False).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
    document = document.replace('__LOCALIZED_DATA__', serialized)
    # Runtime lookup is exact and display-only, for original explanatory fields.
    mapping = json.dumps(ENGLISH if language == 'en' else {}, ensure_ascii=True)
    bootstrap = '<script>window.UI_LANGUAGE=' + json.dumps(language) + ';window.UI_TEXT=' + mapping + ';window.uiText=s=>typeof s==="string"?(UI_TEXT[s]??s):s;</script>'
    document = document.replace('<script>', bootstrap + '<script>', 1)
    opposite = 'zh' if language == 'en' else 'en'
    link = f'<a class="language-switch" id="language-switch" href="{("../../" if review else "../")}{opposite}/{"review/" if review else ""}index.html" lang="{opposite}">{"Chinese" if language == "en" else "English"} ↗</a>'
    if review:
        document = document.replace('<div class="actions">', '<div class="actions">' + link, 1)
    else:
        document = document.replace('<div class="dataset-pill">', link + '<div class="dataset-pill">', 1)
    document = document.replace('</head>', '<style>' + (PACKAGE / 'website_languages.css').read_text() + '</style></head>')
    switch_script = (PACKAGE / 'website_languages.js').read_text()
    document = document.replace('</body>', '<script>' + switch_script + '</script></body>')
    return document
