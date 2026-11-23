import { legacyLocation } from '$lib/rehear-routing.js';
import { sequence } from '@sveltejs/kit/hooks';

import type { Handle } from '@sveltejs/kit';
import { getTextDirection } from '$lib/paraglide/runtime';
import { paraglideMiddleware } from '$lib/paraglide/server';

const handleParaglide: Handle = ({ event, resolve }) =>
	paraglideMiddleware(event.request, ({ request, locale }) => {
		event.request = request;

		return resolve(event, {
			transformPageChunk: ({ html }) =>
				html
					.replace('%paraglide.lang%', locale)
					.replace('%paraglide.dir%', getTextDirection(locale))
					.replace('</head>', event.url.pathname.startsWith('/personal-project/') ? '<script src="/netaq-cache-recovery-v2.js"></script></head>' : '</head>')
		});
	});


const handleLegacyScreens: Handle = async ({ event, resolve }) => {
  const location = legacyLocation(event.url.pathname, event.url.search);
  if (location && (event.request.method === 'GET' || event.request.method === 'HEAD')) {
    return new Response(null, { status: 308, headers: { location } });
  }
  return resolve(event);
};
export const handle: Handle = sequence(handleLegacyScreens, handleParaglide);
