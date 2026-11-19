import { Injectable } from '@angular/core';

type Theme = 'light' | 'dark';
const STORAGE_KEY = 'legacylift_theme';

/** One global source of truth for theme; no per-page hardcoded palette. */
@Injectable({ providedIn: 'root' })
export class ThemeService {
  private current: Theme = 'dark';
  get value(): Theme { return this.current; }

  constructor() {
    let stored: string | null = null;
    try { stored = localStorage.getItem(STORAGE_KEY); } catch { /* private browsing */ }
    const systemLight = typeof window !== 'undefined' &&
      typeof window.matchMedia === 'function' &&
      window.matchMedia('(prefers-color-scheme: light)').matches;
    this.set(stored === 'dark' || stored === 'light' ? stored : (systemLight ? 'light' : 'dark'), false);
  }

  set(theme: Theme, persist = true): void {
    this.current = theme;
    if (typeof document !== 'undefined') {
      document.documentElement.dataset['theme'] = theme;
      document.querySelector('meta[name="theme-color"]')?.setAttribute('content', theme === 'dark' ? '#111214' : '#f3f4f6');
      document.getElementById('legacylift-favicon')?.setAttribute('href', `/branding/official/favicon-${theme}-32x32.png`);
    }
    if (persist) { try { localStorage.setItem(STORAGE_KEY, theme); } catch { /* unavailable */ } }
  }

  toggle(): void { this.set(this.current === 'dark' ? 'light' : 'dark'); }
}
