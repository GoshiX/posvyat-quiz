import hashlib
import json
from pathlib import Path
import shlex
from playwright.sync_api import sync_playwright, expect

root = Path('/home/goshix/quiz')
output = root / 'data/group-video-preparation'
config = {}
for line in (root / '.deploy-server.env').read_text().splitlines():
    if '=' in line and not line.startswith('#'):
        key, value = line.split('=', 1)
        config[key] = shlex.split(value)[0]
base = 'https://posvyat2026.h4ck.me'
uploads = json.loads((output / 'uploaded.json').read_text())['groups']
expected = {g['number']: g['url'] for g in uploads}
report = {'adminPreviews': [], 'mobileCards': [], 'errors': [], 'statePreviewOnly': True}
with sync_playwright() as playwright:
    executable = next(Path('/tmp/quiz-playwright').glob('chromium-*/chrome-linux64/chrome'), None)
    browser = playwright.chromium.launch(headless=True, **({'executable_path': str(executable)} if executable else {}))
    context = browser.new_context(viewport={'width': 1440, 'height': 1000})
    try:
        response = context.request.post(base + '/api/admin/login', data={'username': config['ADMIN_USER'], 'password': config['ADMIN_PASSWORD']})
        assert response.ok, 'Admin login failed'
        response = context.request.get(base + '/api/admin/state')
        assert response.ok
        state = response.json()
        groups = state['groups']
        assert {g['number']:g['mediaUrl'] for g in groups} == expected
        page = context.new_page()
        page.on('pageerror', lambda error: report['errors'].append(str(error)))
        page.goto(base + '/admin')
        page.get_by_test_id('admin-nav-groups').click()
        for group in groups:
            page.locator(f'[data-edit="groups"][data-id="{group["id"]}"]').click()
            expect(page.locator('#group-mediaUrl')).to_have_value(expected[group['number']])
            image = page.locator('[data-preview="group-mediaUrl"] img')
            expect(image).to_be_visible()
            image.evaluate('(img) => img.decode()')
            assert image.evaluate('(img) => img.naturalWidth > 0 && img.naturalHeight > 0')
            report['adminPreviews'].append(group['number'])
        page.screenshot(path=str(output/'admin-media.png'))
        (output/'browser-verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
        print('Admin: all group previews loaded', flush=True)
        mobile = browser.new_context(viewport={'width': 390, 'height': 844}, device_scale_factor=1, is_mobile=True, has_touch=True)
        try:
            own = groups[0]
            payload = {'authenticated': True, 'environment': 'production', 'state': {'phase': 'voting_open', 'currentQuestionId': None, 'runId': state['state']['runId'], 'revision': state['state']['revision']}, 'branding': {'logoUrl': ''}, 'yandexEnabled': False, 'participant': {'id': 'browser-preview-only', 'firstName': 'Просмотр', 'lastName': '', 'groupId': own['id'], 'groupNumber': own['number']}, 'voting': {'submitted': False, 'groups': [g for g in groups if g['id'] != own['id']]}}
            # Only this isolated browser sees voting; no production phase or session is changed.
            mobile.route('**/api/participant*', lambda route: route.fulfill(json=payload))
            page = mobile.new_page()
            page.on('pageerror', lambda error: report['errors'].append(str(error)))
            page.on('requestfailed', lambda request: report.setdefault('failedRequests', []).append({'url': request.url, 'error': request.failure}))
            page.goto(base + '/')
            cards = page.locator('.voting-card')
            expect(cards).to_have_count(len(groups)-1)
            for index, group in enumerate(payload['voting']['groups']):
                card = cards.nth(index)
                image = card.locator('img')
                print('Mobile checking', group['number'], flush=True)
                image.scroll_into_view_if_needed()
                image.evaluate('(img) => new Promise((resolve, reject) => { if (img.complete) return img.naturalWidth ? resolve() : reject(new Error("Image load failed")); img.addEventListener("load", resolve, {once:true}); img.addEventListener("error", () => reject(new Error("Image load failed")), {once:true}); })')
                image.evaluate('(img) => img.decode()')
                assert image.evaluate('(img) => img.naturalWidth > 0')
                assert image.get_attribute('src') == expected[group['number']]
                report['mobileCards'].append(group['number'])
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            cards.first.scroll_into_view_if_needed()
            page.screenshot(path=str(output/'mobile-voting.png'))
            moving_group = next(g for g in payload['voting']['groups'] if g['number'] in ['Б05-601', 'Б05-607'])
            moving_card = page.locator('[data-group="' + moving_group['id'] + '"] img')
            moving_card.scroll_into_view_if_needed()
            first = moving_card.screenshot()
            page.wait_for_timeout(600)
            second = moving_card.screenshot()
            report['animationVisiblyChanges'] = hashlib.sha256(first).digest() != hashlib.sha256(second).digest()
            assert report['animationVisiblyChanges'], 'Animation appears frozen'
            print('Mobile: all cards loaded, animation changes, no overflow', flush=True)
        finally:
            (output/'browser-verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
            mobile.close()
        assert not report['errors']
    finally:
        context.request.post(base + '/api/admin/logout', data={})
        context.close()
        browser.close()
(output/'browser-verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
