import './bootstrap.js';
import { initProxyStatus } from './modules/proxy-status.js';
import { initSearchForm, isScrapingInProgress } from './modules/search-form.js';
import { initInstagramForm } from './modules/instagram-form.js';
import { initLinkedInForm } from './modules/linkedin-form.js';
export { toSafeHttpUrl } from './lib/dom-utils.js';

initSearchForm();
initInstagramForm();
initLinkedInForm();
initProxyStatus({ isScrapingInProgress });
