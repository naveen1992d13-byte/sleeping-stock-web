import React, { useEffect, useState } from 'react';
import { isTestingUiEnabled } from '@/testingUi';

function shortSha(value) {
  const text = String(value || '').trim();
  if (!text) return '';
  return text.slice(0, 7);
}

export function TestingBanner() {
  const enabled = isTestingUiEnabled();
  const [meta, setMeta] = useState(() => ({
    pr_number: process.env.REACT_APP_TESTING_PR || '',
    git_branch: process.env.REACT_APP_TESTING_BRANCH || '',
    commit_short: process.env.REACT_APP_TESTING_COMMIT || '',
    deployed_at_ist: process.env.REACT_APP_TESTING_DEPLOYED_AT || '',
  }));

  useEffect(() => {
    if (!enabled) return undefined;
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch('/deployment.json', { cache: 'no-store' });
        if (!res.ok) return;
        const data = await res.json();
        if (cancelled || !data || data.environment !== 'testing') return;
        setMeta({
          pr_number: data.pr_number || '',
          git_branch: data.git_branch || '',
          commit_short: data.commit_short || shortSha(data.commit),
          deployed_at_ist: data.deployed_at_ist || data.deployed_at || '',
        });
      } catch {
        /* keep build-time fallback */
      }
    })();
    return () => { cancelled = true; };
  }, [enabled]);

  if (!enabled) return null;

  return (
    <div
      data-testid="testing-env-banner"
      role="status"
      style={{
        position: 'sticky',
        top: 0,
        zIndex: 80,
        background: '#92400E',
        color: '#FFFBEB',
        fontSize: 12,
        lineHeight: 1.4,
        padding: '6px 12px',
        display: 'flex',
        flexWrap: 'wrap',
        gap: '8px 16px',
        justifyContent: 'center',
        letterSpacing: '0.01em',
      }}
    >
      <strong>TESTING ENVIRONMENT</strong>
      <span>PR {meta.pr_number || 'n/a'}</span>
      <span>{meta.git_branch || 'unknown-branch'}</span>
      <span>{meta.commit_short || 'unknown-sha'}</span>
      <span>{meta.deployed_at_ist || 'unknown time'}</span>
    </div>
  );
}

export default TestingBanner;
