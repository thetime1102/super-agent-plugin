/**
 * extractor.ts — Smart content extraction from Tree-sitter AST
 * 
 * Features:
 *   - extractSignature: Only function/class signature, no body
 *   - extractSmart: Full body if small, truncated if large
 *   - Preserves JSDoc comments above exported declarations
 */

import { LanguageId } from './parsers/index.js';

export interface SymbolBody {
  symbolName: string;
  kind: string;
  file: string;
  line: number;
  endLine: number;
  body: string;
  truncated: boolean;
}

export type ExtractMode = 'full' | 'signature' | 'smart';

// ─── Signature extraction ──────────────────────────

/**
 * Get the "signature" of a declaration — name + parameters + return type
 * without the body. For classes: name + methods list (signatures only).
 */
function extractSignature(node: any, source: string): { signature: string; endLine: number } {
  switch (node.type) {
    case 'function_declaration':
    case 'generator_function_declaration': {
      const fn = node.childForFieldName('name');
      const fp = node.childForFieldName('parameters');
      const fr = node.childForFieldName('return_type');
      const fName = fn ? source.substring(fn.startIndex, fn.endIndex) : '<anon>';
      const fParams = fp ? source.substring(fp.startIndex, fp.endIndex) : '()';
      const fReturn = fr ? source.substring(fr.startIndex, fr.endIndex) : '';
      const isAsync = source.substring(node.startIndex, node.endIndex).startsWith('async');
      const sig = `${isAsync ? 'async ' : ''}${fName}${fParams}${fReturn};`;
      return { signature: sig, endLine: node.startPosition.row + 1 };
    }

    case 'class_declaration': {
      const cn = node.childForFieldName('name');
      const name = cn ? source.substring(cn.startIndex, cn.endIndex) : '<anon>';
      const body = node.childForFieldName('body');
      const methods: string[] = [];
      if (body) {
        for (let j = 0; j < body.childCount; j++) {
          const m = body.child(j);
          if (m.type === 'method_definition') {
            const mn = m.childForFieldName('name');
            const mp = m.childForFieldName('parameters');
            const mr = m.childForFieldName('return_type');
            const mName = mn ? source.substring(mn.startIndex, mn.endIndex) : '?';
            const mParams = mp ? source.substring(mp.startIndex, mp.endIndex) : '()';
            const mReturn = mr ? source.substring(mr.startIndex, mr.endIndex) : '';
            methods.push(`  ${mName}${mParams}${mReturn};`);
          }
        }
      }
      return { signature: `class ${name} {\n${methods.join('\n')}\n}`, endLine: node.startPosition.row + 1 };
    }

    case 'interface_declaration': {
      const inName = node.childForFieldName('name');
      const name = inName ? source.substring(inName.startIndex, inName.endIndex) : '<anon>';
      return { signature: `interface ${name} { ... }`, endLine: node.startPosition.row + 1 };
    }

    case 'type_alias_declaration': {
      const tn = node.childForFieldName('name');
      const name = tn ? source.substring(tn.startIndex, tn.endIndex) : '<anon>';
      return { signature: `type ${name} = ...`, endLine: node.startPosition.row + 1 };
    }

    default:
      // For other types, just get the first line
      const firstLine = source.substring(node.startIndex, source.indexOf('\n', node.startIndex));
      return { signature: firstLine.trim() + ' ...', endLine: node.startPosition.row + 1 };
  }
}

// ─── JSDoc preservation ────────────────────────────

/**
 * Extract the JSDoc comment preceding a node
 */
function extractJsDoc(node: any, source: string): string {
  const nodeStart = node.startIndex;
  // Look backwards from node start for JSDoc
  const beforeBlock = source.substring(Math.max(0, nodeStart - 2000), nodeStart);
  
  // Match JSDoc pattern: /** ... */ immediately before the declaration
  const jsDocMatch = beforeBlock.match(/\/\*\*[\s\S]*?\*\/\s*$/);
  if (jsDocMatch) {
    // Only include if no statements between JSDoc and node
    const jsDocEnd = beforeBlock.lastIndexOf(jsDocMatch[0]) + jsDocMatch[0].length;
    const between = beforeBlock.substring(jsDocEnd);
    if (!between || /^\s*$/.test(between)) {
      return jsDocMatch[0];
    }
  }
  return '';
}

// ─── Main extractor ────────────────────────────────

/**
 * Extract symbol content based on mode
 */
export function extractSymbol(
  node: any,
  source: string,
  filePath: string,
  symbolName: string,
  kind: string,
  mode: ExtractMode = 'smart',
  languageId?: LanguageId,
): SymbolBody {
  const lineCount = node.endPosition.row - node.startPosition.row + 1;
  const fullBody = source.substring(node.startIndex, node.endIndex);
  const jsDoc = extractJsDoc(node, source);
  
  switch (mode) {
    case 'full':
      return {
        symbolName,
        kind,
        file: filePath,
        line: node.startPosition.row + 1,
        endLine: node.endPosition.row + 1,
        body: fullBody,
        truncated: false,
      };

    case 'signature': {
      const { signature } = extractSignature(node, source);
      const body = jsDoc ? `${jsDoc}\n${signature}` : signature;
      return {
        symbolName,
        kind: 'signature',
        file: filePath,
        line: node.startPosition.row + 1,
        endLine: node.endPosition.row + 1,
        body,
        truncated: true,
      };
    }

    case 'smart': {
      // Signature-only if body > 50 lines
      if (lineCount > 50) {
        const { signature } = extractSignature(node, source);
        const body = jsDoc ? `${jsDoc}\n${signature}` : signature;
        return {
          symbolName,
          kind: 'signature',
          file: filePath,
          line: node.startPosition.row + 1,
          endLine: node.endPosition.row + 1,
          body: `${body}\n// ... truncated (${lineCount} lines total)`,
          truncated: true,
        };
      }
      
      // Full body but preserve JSDoc
      const body = jsDoc && !fullBody.includes(jsDoc) ? `${jsDoc}\n${fullBody}` : fullBody;
      return {
        symbolName,
        kind,
        file: filePath,
        line: node.startPosition.row + 1,
        endLine: node.endPosition.row + 1,
        body,
        truncated: false,
      };
    }
  }
}
