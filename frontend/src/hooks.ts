import type { Reroute } from '@sveltejs/kit';
import { rootRoute } from '$lib/rehear-routing.js';
export const reroute: Reroute = ({ url }) => rootRoute(url.pathname) ?? undefined;
