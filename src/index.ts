/**
 * index.ts — Super Agent Plugin Entry Point
 * 
 * Đăng ký 2 thành phần:
 *   1. Agent Tool `read_code_symbol` → LLM chủ động zoom-in code
 *   2. Context Engine `super-agent` → inject file map khi user mention file
 */

import { definePluginEntry } from 'openclaw/plugin-sdk/plugin-entry';
import { Type } from 'typebox';
import { join, sep, resolve } from 'node:path';
import { existsSync } from 'node:fs';
import { mapFile, readCodeSymbol, detectFileReferences } from './repo-mapper.js';

/**
 * Resolve project root từ config hoặc auto-detect.
 * Bug #5 fix: không dùng process.cwd() trực tiếp mà scan subdirectories.
 * Bug #7 fix: đọc từ api.pluginConfig.projectRoot.
 */
function resolveProjectRoot(cfgRoot?: string): string {
  if (cfgRoot && existsSync(cfgRoot)) return cfgRoot;

  const cwd = process.cwd();

  // If CWD itself is a project root, use it
  if (existsSync(join(cwd, 'package.json')) || existsSync(join(cwd, 'src'))) {
    return cwd;
  }

  // Scan common project directories
  const candidates = [
    // NHAT VI CAKE projects
    join(cwd, 'nhatvi-ecosystem-dev'),
    join(cwd, 'nhatvi-ecosystem'),
    // Generic Next.js / Node.js projects
    join(cwd, 'src'),
    join(cwd, 'app'),
    join(cwd, 'web'),
  ];

  for (const dir of candidates) {
    if (existsSync(dir) && (existsSync(join(dir, 'package.json')) || existsSync(join(dir, 'src')))) {
      return dir;
    }
  }

  return cwd;
}

const entry: any = definePluginEntry({
  id: 'super-agent',
  name: 'Super Agent',
  description: 'Tree-sitter Repo Mapper + Code Symbol Tool + Context Engine',

  register(api) {
    // Đọc plugin config (Bug #7 fix)
    const pluginCfg = (api as any).pluginConfig || {};
    const projectRoot = resolveProjectRoot(pluginCfg.projectRoot as string | undefined);

    // TOOL: read_code_symbol
    api.registerTool({
      name: 'read_code_symbol',
      label: 'Read Code Symbol',
      description: 'Đọc body của một symbol (function, class, interface) từ file TypeScript.',
      parameters: Type.Object({
        filePath: Type.String({ description: 'Đường dẫn file (VD: src/services/llm.service.ts)' }),
        symbolName: Type.String({ description: 'Tên symbol (VD: callDeepSeek)' }),
      }),
      async execute(_id: string, params: unknown) {
        const { filePath, symbolName } = params as any;
        try {
          const rootDir = projectRoot;
          // Bug #8 fix: hỗ trợ cả forward-slash và backslash
          const normalized = filePath.replace(/[\\/]/g, '/');
          const fullPath = normalized.startsWith('/') ? normalized : join(rootDir, normalized);
          const result = await readCodeSymbol(fullPath, symbolName);
          if (!result) {
            return { content: [{ type: 'text', text: `❌ Symbol "${symbolName}" không tìm thấy trong "${filePath}".` }], details: {} };
          }
          return {
            content: [{ type: 'text', text: [
              `📘 Symbol: ${result.symbolName}`,
              `📄 File: ${filePath}`,
              `📍 Lines: ${result.line} - ${result.endLine}`,
              `📏 Length: ${result.body.length} chars`,
              '',
              '```typescript',
              result.body,
              '```',
            ].join('\n') }],
            details: {},
          };
        } catch (err: any) {
          return { content: [{ type: 'text', text: `❌ Error: ${err.message}` }], details: {} };
        }
      },
    });

    // CONTEXT ENGINE: super-agent
    api.registerContextEngine('super-agent', () => ({
      info: {
        id: 'super-agent',
        name: 'Super Agent Engine',
        description: 'Injects file map (imports + declarations) when user mentions a file',
      },
      ingest: async () => ({ ingested: true }),
      compact: async () => ({ ok: true, compacted: true }),

      async assemble({ messages }: any) {
        const lastUserMsg = [...messages].reverse().find((m: any) => m.role === 'user');
        if (!lastUserMsg || typeof lastUserMsg.content !== 'string') {
          return { messages, estimatedTokens: 0 };
        }

        const files = detectFileReferences(lastUserMsg.content);
        if (files.length === 0) return { messages, estimatedTokens: 0 };

        const rootDir = projectRoot;
        const additions: string[] = [];

        for (const fileRef of files.slice(0, 1)) {
          try {
            // Bug #8 fix: normalize backslash paths
            const normalized = fileRef.replace(/[\\/]/g, '/');
            const fullPath = normalized.startsWith('/') ? normalized : join(rootDir, normalized);
            const fileMap = await mapFile(fullPath, rootDir);
            const lines: string[] = [
              `📋 FILE MAP: ${fileMap.file}`,
              `   Size: ${fileMap.size} bytes, ${fileMap.lines} lines`, '',
            ];

            if (fileMap.imports.length > 0) {
              const local = fileMap.imports.filter((i: any) => i.modulePath.startsWith('.'));
              const external = fileMap.imports.filter((i: any) => !i.modulePath.startsWith('.'));
              lines.push(`🔗 Imports (${fileMap.imports.length}):`);
              if (external.length) { lines.push('   📦 External:'); external.forEach((i: any) => lines.push(`      ${i.modulePath}`)); }
              if (local.length) { lines.push('   📁 Local:'); local.forEach((i: any) => lines.push(`      ${i.modulePath}${i.specifiers ? ' → ' + i.specifiers.join(', ') : ''}`)); }
              lines.push('');
            }

            const classes = fileMap.declarations.filter((d: any) => d.kind === 'class');
            const funcs = fileMap.declarations.filter((d: any) => d.kind === 'function');
            const ifaces = fileMap.declarations.filter((d: any) => d.kind === 'interface');
            const types = fileMap.declarations.filter((d: any) => d.kind === 'type_alias');

            lines.push(`📊 Declarations (${fileMap.declarations.length}):`);
            classes.forEach((c: any) => { lines.push(`   📘 ${c.name}`); (c.methods || []).forEach((m: string) => lines.push(`      └─ ${m}`)); });
            funcs.forEach((f: any) => lines.push(`   ⚡ ${f.signature}`));
            ifaces.forEach((i: any) => lines.push(`   📐 ${i.name}`));
            types.forEach((t: any) => lines.push(`   📎 ${t.name}`));
            lines.push('', `💡 Dùng "read_code_symbol" với filePath="${fileRef}" để zoom-in.`);

            additions.push(lines.join('\n'));
          } catch { /* skip */ }
        }

        if (additions.length === 0) return { messages, estimatedTokens: 0 };

        return {
          messages,
          estimatedTokens: Math.ceil(additions[0].length / 4),
          systemPromptAddition: additions.join('\n\n---\n\n'),
        };
      },
    }));
  },
});

export default entry;
