// Small inline-SVG icon glyphs — no emoji anywhere in this app. Every icon
// uses currentColor/stroke so it automatically follows whatever color the
// button it's placed in already uses (theme-aware for free, dark/light).
const ICON_STYLE = 'style="vertical-align:-3px;margin-right:5px"';

export const ICON_HEADPHONES = `<svg ${ICON_STYLE} width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 14v-2a8 8 0 0 1 16 0v2"/><rect x="2.5" y="14" width="4" height="6" rx="1.5"/><rect x="17.5" y="14" width="4" height="6" rx="1.5"/></svg>`;

export const ICON_MIC = `<svg ${ICON_STYLE} width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="2" width="6" height="12" rx="3"/><path d="M5 10v1a7 7 0 0 0 14 0v-1"/><line x1="12" y1="18" x2="12" y2="22"/><line x1="8" y1="22" x2="16" y2="22"/></svg>`;

export const ICON_EMOTE = `<svg ${ICON_STYLE} width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><line x1="8.5" y1="10" x2="8.5" y2="10.6"/><line x1="15.5" y1="10" x2="15.5" y2="10.6"/><path d="M8 14.5c1.1 1.3 2.5 2 4 2s2.9-.7 4-2"/></svg>`;

export const ICON_RECORD = `<svg ${ICON_STYLE} width="13" height="13" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9" fill="currentColor"/></svg>`;
