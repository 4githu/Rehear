/// <reference types="vitest/config" />
import { paraglideVitePlugin } from '@inlang/paraglide-js';
import { defineConfig } from 'vitest/config';
import { playwright } from '@vitest/browser-playwright';
import tailwindcss from '@tailwindcss/vite';
import { sveltekit } from '@sveltejs/kit/vite';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { storybookTest } from '@storybook/addon-vitest/vitest-plugin';
import {
	API_PATHS,
	createViteProxyPattern,
	sanitizeProxyRequestHeaders
} from './scripts/hosting-proxy.mjs';
const dirname =
	typeof __dirname !== 'undefined' ? __dirname : path.dirname(fileURLToPath(import.meta.url));
const devPublicOrigin = process.env.ORIGIN || `http://localhost:${process.env.PORT || 5173}`;

// More info at: https://storybook.js.org/docs/next/writing-tests/integrations/vitest-addon
export default defineConfig({
	optimizeDeps: {
		exclude: ['html2canvas', 'jspdf']
	},
	server: {
		host: process.env.HOST || '127.0.0.1',
		port: Number(process.env.PORT || 5173),
		proxy: Object.fromEntries(
			API_PATHS.map((route) => [
				createViteProxyPattern(route),
				{
					target:
						process.env.API_UPSTREAM_URL ||
						process.env.PERSONAL_API_PROXY ||
						'http://localhost:8000',
					changeOrigin: false,
					configure: (proxy) => {
						proxy.on('proxyReq', (proxyReq) => {
							sanitizeProxyRequestHeaders(proxyReq, devPublicOrigin);
						});
					}
				}
			])
		)
	},
	plugins: [
		tailwindcss(),
		sveltekit(),
		paraglideVitePlugin({
			project: './project.inlang',
			outdir: './src/lib/paraglide'
		})
	],
	test: {
		expect: {
			requireAssertions: true
		},
		projects: [
			{
				extends: './vite.config.ts',
				test: {
					name: 'client',
					browser: {
						enabled: true,
						provider: playwright(),
						instances: [
							{
								browser: 'chromium',
								headless: true
							}
						]
					},
					include: ['src/**/*.svelte.{test,spec}.{js,ts}'],
					exclude: ['src/lib/server/**']
				}
			},
			{
				extends: './vite.config.ts',
				test: {
					name: 'server',
					environment: 'node',
					include: ['src/**/*.{test,spec}.{js,ts}'],
					exclude: ['src/**/*.svelte.{test,spec}.{js,ts}']
				}
			},
			{
				extends: true,
				plugins: [
					// The plugin will run tests for the stories defined in your Storybook config
					// See options at: https://storybook.js.org/docs/next/writing-tests/integrations/vitest-addon#storybooktest
					storybookTest({
						configDir: path.join(dirname, '.storybook')
					})
				],
				test: {
					name: 'storybook',
					browser: {
						enabled: true,
						headless: true,
						provider: playwright({}),
						instances: [
							{
								browser: 'chromium'
							}
						]
					}
				}
			}
		]
	}
});
