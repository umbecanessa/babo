export interface ExplorerNode {
  name: string;
  path: string;
  isDirectory: boolean;
  children?: ExplorerNode[];
  expanded?: boolean;
  loading?: boolean;
}

export interface EditorTab {
  path: string;
  name: string;
  language: string;
  content: string;
  dirty: boolean;
  loading?: boolean;
  /** Monaco model — owned by the editor component. */
  model?: unknown;
}
