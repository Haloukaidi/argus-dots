import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { Snapshot } from '../api';
import { ResultSummary } from '../components/ResultSummary';
import { TopBar } from '../components/TopBar';
import { I18nProvider } from '../i18n';

const nativeSnapshot: Snapshot = {
  session: { id: 'native-fixture', display_name: 'Native fixture', objective: '', last_active: 1,
    cwd: '/fixture/project', workdir: '/fixture/project' },
  daemon: { alive: false, pid: null, uptime_seconds: null, backend: 'dots', global_daily_cap_usd: null },
  roles: [], backlog: [], recent_events: [],
  spend_usd: null, spend_status: 'empty',
  usage_summary: {
    call_count: 0, known_cost_usd: 0, cost_usd: null, pricing_status: 'empty',
    priced_calls: 0, partial_calls: 0, unpriced_calls: 0, not_billed_calls: 0,
    input_tokens: 0, cached_input_tokens: 0, cache_write_tokens: 0, output_tokens: 0,
    reasoning_output_tokens: 0, premium_requests: 0, total_nano_aiu: 0, premium_request_cost_usd: 0,
  },
};

afterEach(() => vi.unstubAllGlobals());

describe('native dots usage without a provider ledger', () => {
  it('does not turn an empty ledger into a measured zero spend badge', () => {
    vi.stubGlobal('localStorage', { getItem: () => 'en' });
    const html = renderToStaticMarkup(
      <I18nProvider>
        <TopBar snap={nativeSnapshot} streamOk onStart={vi.fn()} onStop={vi.fn()}
          onManage={vi.fn()} busy={false} />
      </I18nProvider>,
    );
    expect(html).toContain('Native fixture');
    expect(html).not.toContain('$0');
    expect(html).not.toContain('0 model calls');
    expect(html).not.toContain('Project spend');
  });

  it('keeps a completed native result without fabricating a cost', () => {
    const html = renderToStaticMarkup(<ResultSummary entries={[{
      id: 'native-result', ts: 1, kind: 'mission_complete', title: 'Fixture completed', summary: '', tags: [],
      extra: { cost_usd: null, pricing_status: 'empty' },
    }]} />);
    expect(html).toContain('Fixture completed');
    expect(html).toContain('Latest result');
    expect(html).not.toContain('$0');
  });
});
