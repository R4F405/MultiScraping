import './bootstrap.js';
import { initProxyStatus } from './modules/proxy-status.js';
import { initSearchForm, isScrapingInProgress } from './modules/search-form.js';
import { initLinkedInForm } from './modules/linkedin-form.js';
export { toSafeHttpUrl } from './lib/dom-utils.js';

initSearchForm();
initLinkedInForm();
initProxyStatus({ isScrapingInProgress });
