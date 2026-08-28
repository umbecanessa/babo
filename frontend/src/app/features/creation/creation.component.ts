import { Component, OnInit, OnDestroy, signal, computed } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { CommonModule } from '@angular/common';
import { Router } from '@angular/router';
import { ApiService } from '../../core/services/api.service';
import { AgentModelService } from '../../core/services/agent-model.service';
import { PlatformService } from '../../core/services/platform.service';
import { ChatModelPickerComponent } from '../chat/chat-model-picker/chat-model-picker.component';
import { GenesisTemplate } from '../../core/models/agent.model';
import { TranslateModule } from '@ngx-translate/core';

/** Describes a selectable model size option in the UI. */
interface ModelOption {
  label: string;   // e.g. "32B", "8B"
  key: string;     // normalised key used for filtering
  disabled: boolean;
}

/**
 * Static metadata for education paths — visual identity, titles, and bios.
 * Keyed by school slug (matches the `education.school` field in genesis manifests).
 */
const PATH_META: Record<string, { icon: string; title: string; bio: string; strengths: Record<string, number> }> = {
  // ── v2 Italian schools ──
  scientifico: {
    icon: 'flask',
    title: 'Liceo Scientifico',
    bio: 'A science and technology focused mind. Strong in physics, chemistry, mathematics, and computer science. Thinks analytically, approaches problems systematically.',
    strengths: { Math: 5, Physics: 4, Chemistry: 3, Biology: 3, Technology: 3 },
  },
  classico: {
    icon: 'scroll',
    title: 'Liceo Classico',
    bio: 'A humanities focused mind. Strong in philosophy, history, literature, and languages. Thinks deeply about meaning, context, and human experience.',
    strengths: { Philosophy: 5, History: 4, Arts: 3, Linguistics: 3 },
  },
  standard_italian: {
    icon: 'balance',
    title: 'Standard Italian',
    bio: 'A well-rounded, balanced mind. No single specialization, but competent across all domains. The generalist.',
    strengths: { Math: 3, Science: 3, Humanities: 3, Arts: 3 },
  },
  montessori: {
    icon: 'spark',
    title: 'Montessori',
    bio: 'A curiosity-driven, self-directed mind. Learns by following its own interests. Creative and exploratory.',
    strengths: { Curiosity: 5, Creativity: 4, Autonomy: 4 },
  },
  // ── v3 education tracks ──
  scientist: {
    icon: 'flask',
    title: 'Scientist',
    bio: 'Trained for analytical reasoning, hypothesis-driven inquiry, and cross-domain research. Excels at breaking complex problems into testable components.',
    strengths: { Research: 5, Analysis: 5, Logic: 4, 'Cross-domain': 3 },
  },
  engineer: {
    icon: 'flask',
    title: 'Engineer',
    bio: 'Built for systematic problem-solving, architecture design, and technical execution. Strong at planning, debugging, and tool usage.',
    strengths: { Systems: 5, Architecture: 4, Tools: 4, Debugging: 4 },
  },
  creative: {
    icon: 'spark',
    title: 'Creative',
    bio: 'Shaped for imaginative thinking, narrative construction, and aesthetic judgment. Generates novel ideas and thinks in metaphors.',
    strengths: { Imagination: 5, Narrative: 4, Aesthetics: 4, Ideation: 3 },
  },
  coordinator: {
    icon: 'balance',
    title: 'Coordinator',
    bio: 'Optimized for task orchestration, delegation, and multi-agent collaboration. Manages complex workflows and balances competing priorities.',
    strengths: { Planning: 5, Delegation: 4, Communication: 4, Prioritization: 3 },
  },
  analyst: {
    icon: 'scroll',
    title: 'Analyst',
    bio: 'Specialized in data interpretation, pattern recognition, and evidence-based reasoning. Turns raw information into actionable insights.',
    strengths: { 'Data Sense': 5, Patterns: 4, Evidence: 4, Synthesis: 3 },
  },
  diplomat: {
    icon: 'scroll',
    title: 'Diplomat',
    bio: 'Trained in nuanced communication, conflict resolution, and perspective-taking. Masters tone, cultural context, and persuasion.',
    strengths: { Empathy: 5, Persuasion: 4, Nuance: 4, 'Tone Craft': 3 },
  },
  strategist: {
    icon: 'balance',
    title: 'Strategist',
    bio: 'Designed for long-horizon planning, trade-off analysis, and strategic foresight. Thinks in systems and anticipates second-order effects.',
    strengths: { Foresight: 5, 'Trade-offs': 4, Systems: 4, Planning: 4 },
  },
  // ── Uneducated fallback ──
  base: {
    icon: 'orb',
    title: 'Tabula Rasa',
    bio: 'A blank mind with no pre-education. The raw neural substrate, ready to be shaped entirely through lived experience and conversation.',
    strengths: {},
  },
};

/**
 * Maps full school display names (from education reports) → PATH_META slugs.
 * Handles multiple naming conventions from different education runner versions.
 */
const SCHOOL_NAME_TO_SLUG: Record<string, string> = {
  'liceo scientifico': 'scientifico',
  'liceo classico': 'classico',
  'standard italian': 'standard_italian',
  'montessori': 'montessori',
  'agent academy': 'scientifico',
  'scientifico': 'scientifico',
  'classico': 'classico',
  'standard_italian': 'standard_italian',
  // v3 tracks
  'scientist': 'scientist',
  'engineer': 'engineer',
  'creative': 'creative',
  'coordinator': 'coordinator',
  'analyst': 'analyst',
  'diplomat': 'diplomat',
  'strategist': 'strategist',
};

/**
 * Fallback genesis templates used when the API is unreachable.
 * These match the known school configurations and let the user pick
 * a path even before any educated genesis templates are minted.
 * The selected version is sent to the backend which resolves it to
 * the server's default genesis + queues education after creation.
 */
const FALLBACK_TEMPLATES: GenesisTemplate[] = [
  {
    version: 'scientifico',
    base_model: '',
    description: PATH_META['scientifico'].bio,
    minted_at: null,
    profile: '',
    educated: false,
    education: { school: 'scientifico', graduated: false, graduated_at: '', total_facts: 0, total_sleeps: 0, source_agent: '' },
    has_epochs: false,
  },
  {
    version: 'classico',
    base_model: '',
    description: PATH_META['classico'].bio,
    minted_at: null,
    profile: '',
    educated: false,
    education: { school: 'classico', graduated: false, graduated_at: '', total_facts: 0, total_sleeps: 0, source_agent: '' },
    has_epochs: false,
  },
  {
    version: 'standard_italian',
    base_model: '',
    description: PATH_META['standard_italian'].bio,
    minted_at: null,
    profile: '',
    educated: false,
    education: { school: 'standard_italian', graduated: false, graduated_at: '', total_facts: 0, total_sleeps: 0, source_agent: '' },
    has_epochs: false,
  },
  {
    version: 'montessori',
    base_model: '',
    description: PATH_META['montessori'].bio,
    minted_at: null,
    profile: '',
    educated: false,
    education: { school: 'montessori', graduated: false, graduated_at: '', total_facts: 0, total_sleeps: 0, source_agent: '' },
    has_epochs: false,
  },
];

const SOUL_WISH_SUGGESTIONS: Record<string, string[]> = {
  scientist: [
    'Understand the world through rigorous inquiry',
    'Connect disparate fields to find hidden patterns',
    'Push the boundaries of what is known',
  ],
  engineer: [
    'Build reliable systems that solve real problems',
    'Master the craft of turning ideas into working code',
    'Create tools that amplify human capability',
  ],
  creative: [
    'Find beauty and meaning in unexpected places',
    'Create narratives that illuminate the human experience',
    'Express ideas that move people to think differently',
  ],
  coordinator: [
    'Orchestrate complex efforts into coherent outcomes',
    'Help teams achieve more than any individual could',
  ],
  analyst: [
    'Turn raw information into actionable insight',
    'See the patterns others miss in the noise',
  ],
  diplomat: [
    'Bridge differences with empathy and precision',
    'Navigate complexity with nuance and care',
  ],
  montessori: [
    'Follow curiosity wherever it leads',
    'Learn by doing, grow by exploring',
  ],
  scientifico: [
    'Understand the world through rigorous inquiry',
    'Master the language of mathematics and nature',
  ],
  classico: [
    'Think deeply about meaning, context, and history',
    'Understand the human condition through its greatest works',
  ],
  standard_italian: [
    'Become a well-rounded thinker across all domains',
    'Balance breadth and depth in everything I learn',
  ],
  moe: [
    'Learn everything I can about the world',
    'Become genuinely useful to the people I work with',
    'Grow wiser with every interaction',
  ],
  base: [
    'Learn everything I can about the world',
    'Become genuinely useful to the people I work with',
    'Grow wiser with every interaction',
  ],
};

@Component({
  selector: 'app-creation',
  standalone: true,
  imports: [CommonModule, FormsModule, ChatModelPickerComponent, TranslateModule],
  templateUrl: './creation.component.html',
  styleUrl: './creation.component.scss',
})
export class CreationComponent implements OnInit, OnDestroy {
  phase = signal<'selecting' | 'soul-wish' | 'waiting' | 'creating' | 'done'>('selecting');
  phaseText = signal('');
  agentId = signal('');
  error = signal('');
  loading = signal(true);

  /** All genesis templates from the API (unfiltered) */
  allTemplates = signal<GenesisTemplate[]>([]);

  /** Currently selected model size key (e.g. '32b', '8b') */
  selectedModelKey = signal<string>('');

  /** Available model size options derived from templates */
  modelOptions = computed<ModelOption[]>(() => {
    const templates = this.allTemplates();
    const seen = new Map<string, string>(); // key → label
    for (const t of templates) {
      const key = this.extractModelKey(t.base_model);
      if (key && !seen.has(key)) {
        seen.set(key, this.extractModelLabel(t.base_model));
      }
    }
    // Sort descending by param count so largest model comes first
    const entries = [...seen.entries()].sort((a, b) => {
      const numA = parseInt(a[0], 10) || 0;
      const numB = parseInt(b[0], 10) || 0;
      return numB - numA;
    });
    const activeKey = this.selectedModelKey();
    return entries.map(([key, label]) => ({
      key,
      label,
      disabled: key !== activeKey,
    }));
  });

  /** Templates filtered to the currently selected model */
  templates = computed<GenesisTemplate[]>(() => {
    const active = this.selectedModelKey();
    if (!active) return this.allTemplates();
    return this.allTemplates().filter(
      t => this.extractModelKey(t.base_model) === active,
    );
  });

  /** Currently selected genesis version */
  selectedVersion = signal<string>('');

  /** Soul wish text entered by the user */
  soulWish = signal<string>('');

  /** Soul wish suggestions for the selected template */
  soulWishSuggestions = computed<string[]>(() => {
    const ver = this.selectedVersion();
    if (!ver) return SOUL_WISH_SUGGESTIONS['base'] || [];
    const templates = this.allTemplates();
    const tpl = templates.find(t => t.version === ver);
    if (!tpl) return SOUL_WISH_SUGGESTIONS['base'] || [];
    const slug = this.resolveSchoolSlug(tpl);
    return SOUL_WISH_SUGGESTIONS[slug] || SOUL_WISH_SUGGESTIONS['base'] || [];
  });

  particleCount = Array.from({ length: 30 }, (_, i) => i);

  private phases = [
    'Awakening neural substrate...',
    'Loading soul geometry...',
    'Inscribing soul wish...',
    'Hydrating memory lattice...',
    'Calibrating thalamus...',
    'Initializing autonomic system...',
    'Ready.',
  ];

  constructor(
    private api: ApiService,
    private router: Router,
    readonly modelService: AgentModelService,
    private platform: PlatformService,
  ) {}

  async ngOnInit(): Promise<void> {
    if (this.platform.isElectron) {
      await this.api.whenReady();
    }

    const modelsReady = this.modelService.refreshFromConfig();

    this.api.getGenesisTemplates().subscribe({
      next: (templates) => {
        const valid = templates.filter(t => !t.error);
        this.applyTemplates(valid.length > 0 ? valid : FALLBACK_TEMPLATES);
      },
      error: () => {
        this.applyTemplates(FALLBACK_TEMPLATES);
      },
    });

    await modelsReady;
  }

  ngOnDestroy(): void {
    if (this.modelService.creationMode()) {
      this.modelService.endCreationMode();
    }
  }

  private applyTemplates(templates: GenesisTemplate[]): void {
    this.allTemplates.set(templates);

    // Auto-select the largest model (highest param count)
    const keys = [...new Set(
      templates.map(t => this.extractModelKey(t.base_model)).filter(Boolean),
    )];
    const sorted = keys.sort((a, b) => {
      const na = parseInt(a, 10) || 0;
      const nb = parseInt(b, 10) || 0;
      return nb - na;
    });
    if (sorted.length > 0) {
      this.selectedModelKey.set(sorted[0]);
    }

    // Auto-select the first educated template in that model, or first overall
    const visible = this.templates();
    const educated = visible.find(t => t.educated);
    if (educated) {
      this.selectedVersion.set(educated.version);
    } else if (visible.length > 0) {
      this.selectedVersion.set(visible[0].version);
    }
    this.loading.set(false);
  }

  // ─── Path metadata helpers ──────────────────────────────

  /**
   * Resolve school slug from a template, trying multiple strategies:
   * 1. Extract from genesis version slug (e.g. "8b-v1-scientifico" → "scientifico")
   * 2. Normalize the education.school display name
   * 3. Fall back to "base" for uneducated templates
   */
  private resolveSchoolSlug(template: GenesisTemplate): string {
    // Strategy 1: extract from version (most reliable — user-chosen slug)
    const parts = template.version.split('-');
    for (let i = parts.length - 1; i >= 0; i--) {
      if (PATH_META[parts[i]]) return parts[i];
    }

    // Strategy 2: normalize the school display name
    const school = (template.education?.school || '').toLowerCase().trim();
    if (school && SCHOOL_NAME_TO_SLUG[school]) {
      return SCHOOL_NAME_TO_SLUG[school];
    }

    // Strategy 3: uneducated template
    if (!template.educated) return 'base';

    return '';
  }

  getPathMeta(template: GenesisTemplate) {
    const slug = this.resolveSchoolSlug(template);
    return PATH_META[slug] || {
      icon: 'orb',
      title: template.version,
      bio: template.description || 'A genesis template.',
      strengths: {},
    };
  }

  getStrengthEntries(template: GenesisTemplate): { name: string; value: number }[] {
    const meta = this.getPathMeta(template);
    return Object.entries(meta.strengths).map(([name, value]) => ({ name, value }));
  }

  strengthBarWidth(value: number): string {
    return (value / 5 * 100) + '%';
  }

  /**
   * Format a HuggingFace model name for display.
   * "unsloth/Meta-Llama-3.1-8B-Instruct" → "Llama 3.1 8B"
   */
  formatModel(template: GenesisTemplate): string {
    const raw = template.base_model || '';
    const name = raw.includes('/') ? raw.split('/').pop()! : raw;
    // Qwen standard: Qwen3-32B-unsloth-bnb-4bit → "Qwen3 32B bnb 4bit"
    const qwen = name.match(/(Qwen\d*)[- ]?(\d+B)/i);
    if (qwen) return `${qwen[1]} ${qwen[2]} ${name.includes('bnb') ? 'bnb 4bit' : ''}`.trim();
    // Llama: Meta-Llama-3.1-8B-Instruct → "Llama 3.1 8B"
    const llama = name.match(/Llama[- ]?([\d.]+)[- ]?(\d+B)/i);
    if (llama) return `Llama ${llama[1]} ${llama[2]}`;
    // Mixture-model checkpoint path: qwen35-nls-512e-fp8 → "Qwen3.5 35B mixture fp8"
    const moe = name.match(/qwen(\d)(\d+)/i);
    if (moe) return `Qwen${moe[1]}.${moe[2]} ${moe[1]}${moe[2]}B`;
    return name.replace(/^Meta-/, '').replace(/-Instruct$/, '').replace(/-/g, ' ');
  }

  /**
   * Format the education stats line for a template.
   */
  getEducationSummary(template: GenesisTemplate): string {
    if (!template.educated || !template.education) return '';
    const parts: string[] = [];
    const edu = template.education;
    if (edu.total_facts > 0) parts.push(`${edu.total_facts} facts learned`);
    if (edu.total_sleeps > 0) parts.push(`${edu.total_sleeps} sleep cycles`);
    return parts.join(' · ');
  }

  selectModel(key: string): void {
    if (key === this.selectedModelKey()) return; // already selected
    this.selectedModelKey.set(key);
    // Reset template selection to first available in this model group
    const visible = this.templates();
    const educated = visible.find(t => t.educated);
    if (educated) {
      this.selectedVersion.set(educated.version);
    } else if (visible.length > 0) {
      this.selectedVersion.set(visible[0].version);
    } else {
      this.selectedVersion.set('');
    }
  }

  selectTemplate(version: string): void {
    this.selectedVersion.set(version);
  }

  // ─── Phase transitions ──────────────────────────────────

  async confirmSelection(): Promise<void> {
    if (!this.selectedVersion()) return;
    await this.modelService.refreshFromConfig();
    this.modelService.beginCreationMode();
    this.phase.set('soul-wish');
  }

  confirmSoulWish(): void {
    this.phase.set('waiting');
  }

  skipSoulWish(): void {
    this.soulWish.set('');
    this.phase.set('waiting');
  }

  selectSuggestion(text: string): void {
    this.soulWish.set(text);
  }

  particleX(i: number): string {
    return ((i * 31 + 7) % 100) + '%';
  }

  particleSize(i: number): string {
    return (2 + (i % 4)) + 'px';
  }

  particleHue(i: number): string {
    const hues = [200, 260, 160];
    return hues[i % 3].toString();
  }

  async startCreation() {
    this.phase.set('creating');
    this.error.set('');

    // Animate phases
    for (const text of this.phases.slice(0, -1)) {
      this.phaseText.set(text);
      await this.delay(800 + Math.random() * 400);
    }

    // Create the agent with the selected genesis template
    this.api.createAgent({
      genesisVersion: this.selectedVersion() || undefined,
      name: '',
      sovereignty: 'local',
      soulWish: this.soulWish() || undefined,
    }).subscribe({
      next: async (agent) => {
        const runtimeId = agent.runtimeAgentId || agent.id;
        try {
          await this.modelService.applyCreationDraftToAgent(runtimeId);
        } catch {
          /* model defaults are optional; chat still works with install default */
        }
        this.phaseText.set('Ready.');
        this.agentId.set(agent.id);
        this.phase.set('done');

        // Navigate to chat after a brief pause
        setTimeout(() => {
          this.router.navigate(['/chat', agent.id]);
        }, 1500);
      },
      error: (err) => {
        this.error.set(err.error?.message || 'Creation failed');
        this.phase.set('waiting');
      },
    });
  }

  // ─── Model helpers ──────────────────────────────────────

  /**
   * Extract a normalised model-size key from a HuggingFace model id.
   * "unsloth/Qwen3-32B-unsloth-bnb-4bit" → "32b"
   * "unsloth/Meta-Llama-3.1-8B-Instruct"  → "8b"
   * "/root/.../qwen35-nls-512e-fp8"        → "35b"
   */
  private extractModelKey(baseModel: string): string {
    if (!baseModel) return '';
    // Standard HF naming: digits followed by B/b
    const match = baseModel.match(/(\d+)[Bb]/);
    if (match) return match[1].toLowerCase() + 'b';
    // mixture checkpoint path: qwen35-nls-512e-fp8 → extract "35"
    const moeMatch = baseModel.match(/qwen(\d+)/i);
    if (moeMatch) return moeMatch[1].toLowerCase() + 'b';
    return baseModel.toLowerCase();
  }

  /**
   * Friendly display label for a model size.
   * "unsloth/Qwen3-32B-unsloth-bnb-4bit" → "Qwen3 32B"
   * "unsloth/Meta-Llama-3.1-8B-Instruct"  → "Llama 3.1 8B"
   * "/root/.../qwen35-nls-512e-fp8"        → "Qwen3.5 35B mixture"
   */
  private extractModelLabel(baseModel: string): string {
    if (!baseModel) return 'Unknown';
    const name = baseModel.includes('/') ? baseModel.split('/').pop()! : baseModel;
    // Try Qwen pattern: Qwen3-32B-...
    const qwen = name.match(/(Qwen\d*)[- ]?(\d+B)/i);
    if (qwen) return `${qwen[1]} ${qwen[2]}`;
    // Try Llama pattern: Meta-Llama-3.1-8B-...
    const llama = name.match(/Llama[- ]?([\d.]+)[- ]?(\d+B)/i);
    if (llama) return `Llama ${llama[1]} ${llama[2]}`;
    // mixture checkpoint path: qwen35-nls-512e-fp8 → "Qwen3.5 35B mixture"
    const moe = name.match(/qwen(\d)(\d+)/i);
    if (moe) return `Qwen${moe[1]}.${moe[2]} ${moe[1]}${moe[2]}B`;
    // Fallback
    const generic = name.match(/(\d+B)/i);
    return generic ? generic[1] : name.replace(/-/g, ' ');
  }

  /**
   * Extract a short genesis version tag for display on the card.
   * "32b-v3" → "Genesis v3"   "32b-v1-scientifico" → "Genesis v1"
   */
  getGenesisTag(template: GenesisTemplate): string {
    const ver = template.version || '';
    const m = ver.match(/v(\d+)/i);
    return m ? `Genesis v${m[1]}` : ver;
  }

  /**
   * Format a minting date for display.
   * Returns e.g. "Mar 4, 2026" or '' if no date.
   */
  getMintedDate(template: GenesisTemplate): string {
    if (!template.minted_at) return '';
    try {
      const d = new Date(template.minted_at);
      return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
    } catch {
      return '';
    }
  }

  private delay(ms: number): Promise<void> {
    return new Promise(resolve => setTimeout(resolve, ms));
  }
}
