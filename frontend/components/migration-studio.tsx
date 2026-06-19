'use client';

import * as React from 'react';
import {
  ArrowRight,
  CloudUpload,
  Copy,
  FileCode2,
  FileJson2,
  Loader2,
  Play,
  RefreshCcw,
  Upload,
} from 'lucide-react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Separator } from '@/components/ui/separator';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Textarea } from '@/components/ui/textarea';
import { ArchitectureDiagrams } from '@/components/architecture-diagrams';

type JobState = {
  id: string;
  status: string;
  progress: number;
  created_at: string;
  updated_at: string;
  logs: string[];
  result?: {
    cloudformation?: string;
    runbook?: string;
  } | null;
  error?: string | null;
};

type SampleKind = 'json' | 'terraform';

type DeployApiResponse = {
  status: string;
  logs: string[];
  result?: {
    stack_name?: string;
    region?: string;
    action?: string;
    status?: string;
  } | null;
  error?: string | null;
};

type RepoRunApiResponse = {
  status: string;
  instance_id: string;
  region: string;
  stack_name: string;
  app_dir: string;
  command_id?: string | null;
  stdout?: string | null;
  stderr?: string | null;
  error?: string | null;
};

type DeployedTarget = {
  stackName: string;
  region: string;
};

const BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL ?? 'http://127.0.0.1:8000';

const SAMPLE_JSON = `{
  "migration_id": "mig-20260401-template",
  "source": { "provider": "gcp", "region": "us-central1" },
  "target": { "provider": "aws", "region": "us-east-1" },
  "resources": [
    {
      "id": "gcp-compute-api",
      "source_native_id": "projects/my-project/zones/us-central1-a/instances/api-server",
      "category": "compute",
      "gcp_resource_type": "google_compute_instance",
      "depends_on": ["gcp-subnet-app", "gcp-firewall-app"],
      "config": {
        "name": "api-server",
        "machine_type": "n2-standard-2",
        "boot_disk_size_gb": 50,
        "boot_disk_type": "pd-ssd"
      }
    },
    {
      "id": "gcp-storage-app",
      "source_native_id": "projects/my-project/buckets/app-data-prod",
      "category": "storage",
      "gcp_resource_type": "google_storage_bucket",
      "config": { "name": "app-data-prod", "versioning_enabled": true }
    }
  ],
  "batches": [["gcp-compute-api"], ["gcp-storage-app"]]
}`;

const SAMPLE_TERRAFORM = `resource "google_compute_instance" "web_server" {
  name         = "web-server"
  machine_type = "n1-standard-2"
  zone         = "us-central1-a"

  boot_disk {
    initialize_params {
      image = "debian-cloud/debian-11"
    }
  }

  network_interface {
    network = "default"
    access_config {}
  }
}

resource "google_storage_bucket" "assets" {
  name     = "my-app-assets"
  location = "US"
}`;

function formatLabel(status: string) {
  switch (status) {
    case 'queued':
      return 'Queued';
    case 'running':
      return 'Running';
    case 'completed':
      return 'Completed';
    case 'failed':
      return 'Failed';
    default:
      return status;
  }
}

function statusTone(status: string) {
  switch (status) {
    case 'completed':
      return 'secondary';
    case 'failed':
      return 'destructive';
    case 'running':
      return 'default';
    default:
      return 'outline';
  }
}

export function MigrationStudio() {
  const [sampleKind, setSampleKind] = React.useState<SampleKind>('json');
  const [jsonSourceText, setJsonSourceText] = React.useState(SAMPLE_JSON);
  const [terraformSourceText, setTerraformSourceText] = React.useState(SAMPLE_TERRAFORM);
  const [job, setJob] = React.useState<JobState | null>(null);
  const [isSubmitting, setIsSubmitting] = React.useState(false);
  const [isPolling, setIsPolling] = React.useState(false);
  const [isDeploying, setIsDeploying] = React.useState(false);
  const [isRunningRepo, setIsRunningRepo] = React.useState(false);
  const [copyState, setCopyState] = React.useState<'idle' | 'copied'>('idle');
  const [fetchError, setFetchError] = React.useState<string | null>(null);
  const [deployLogs, setDeployLogs] = React.useState<string[]>([]);
  const [deploySummary, setDeploySummary] = React.useState<string | null>(null);
  const [deployedTarget, setDeployedTarget] = React.useState<DeployedTarget | null>(null);
  const [githubUrl, setGithubUrl] = React.useState('');
  const [repoBranch, setRepoBranch] = React.useState('main');
  const [setupCommand, setSetupCommand] = React.useState('');
  const [startCommand, setStartCommand] = React.useState('python3 -m http.server 8001');
  const [repoRunResult, setRepoRunResult] = React.useState<RepoRunApiResponse | null>(null);
  const fileInputRef = React.useRef<HTMLInputElement | null>(null);

  const sourceText = sampleKind === 'json' ? jsonSourceText : terraformSourceText;
  const setSourceText = sampleKind === 'json' ? setJsonSourceText : setTerraformSourceText;

  React.useEffect(() => {
    if (!job || job.status === 'completed' || job.status === 'failed') {
      return;
    }

    setIsPolling(true);
    const interval = window.setInterval(async () => {
      try {
        const response = await fetch(`${BACKEND_URL}/api/migrations/${job.id}`);
        if (!response.ok) {
          throw new Error(`Status ${response.status}`);
        }
        const data = (await response.json()) as JobState;
        setJob(data);
        setFetchError(null);
        if (data.status === 'completed' || data.status === 'failed') {
          setIsPolling(false);
          window.clearInterval(interval);
        }
      } catch (error) {
        setFetchError(error instanceof Error ? error.message : 'Unable to fetch job status');
      }
    }, 2000);

    return () => {
      window.clearInterval(interval);
      setIsPolling(false);
    };
  }, [job?.id, job?.status]);

  const status = job?.status ?? 'idle';
  const progress = job?.progress ?? 0;
  const cloudformation = job?.result?.cloudformation ?? '';
  const runbook = job?.result?.runbook ?? '';

  async function submitMigration() {
    const trimmed = sourceText.trim();
    if (!trimmed) {
      setFetchError('Paste a Terraform file or GCP JSON payload first.');
      return;
    }

    setIsSubmitting(true);
    setFetchError(null);
    setDeploySummary(null);
    setDeployLogs([]);
    setDeployedTarget(null);
    setRepoRunResult(null);

    try {
      const response = await fetch(`${BACKEND_URL}/api/migrations`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ source: trimmed }),
      });

      if (!response.ok) {
        const message = await response.text();
        throw new Error(message || `Request failed with ${response.status}`);
      }

      const data = (await response.json()) as JobState;
      setJob(data);
    } catch (error) {
      setFetchError(error instanceof Error ? error.message : 'Unable to start migration');
    } finally {
      setIsSubmitting(false);
    }
  }

  async function deployStack() {
    const template = cloudformation.trim();
    if (!template) {
      setFetchError('Run migration first so there is CloudFormation output to deploy.');
      return;
    }

    setIsDeploying(true);
    setFetchError(null);
    setDeploySummary(null);
    setDeployLogs([]);
    setDeployedTarget(null);

    try {
      const response = await fetch(`${BACKEND_URL}/api/deploy`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ cloudformation: template }),
      });

      const data = (await response.json()) as DeployApiResponse;
      if (!response.ok) {
        const message = 'detail' in (data as unknown as Record<string, unknown>)
          ? String((data as unknown as Record<string, unknown>).detail)
          : `Deploy request failed with ${response.status}`;
        throw new Error(message);
      }

      setDeployLogs(data.logs ?? []);

      if (data.status !== 'completed') {
        throw new Error(data.error || 'Deployment failed. Check deploy logs.');
      }

      const summary = data.result
        ? `Deployed stack ${data.result.stack_name ?? 'unknown'} in ${data.result.region ?? 'unknown'} (${data.result.action ?? 'deploy'}).`
        : 'Deployment completed.';
      setDeploySummary(summary);

      const stackName = data.result?.stack_name?.trim();
      const region = data.result?.region?.trim();
      if (!stackName || !region) {
        throw new Error('Deploy succeeded but stack/region metadata is missing. Cannot target EC2 safely.');
      }
      setDeployedTarget({ stackName, region });
    } catch (error) {
      setFetchError(error instanceof Error ? error.message : 'Unable to deploy stack');
    } finally {
      setIsDeploying(false);
    }
  }

  async function runGithubRepoOnEc2() {
    const trimmedUrl = githubUrl.trim();
    const trimmedStart = startCommand.trim();
    if (!trimmedUrl) {
      setFetchError('Enter a GitHub repository URL to run on EC2.');
      return;
    }
    if (!trimmedStart) {
      setFetchError('Enter a start command for the repository.');
      return;
    }
    if (!deployedTarget) {
      setFetchError('Deploy first in this session so stack and region are known for safe EC2 targeting.');
      return;
    }

    setIsRunningRepo(true);
    setFetchError(null);
    setRepoRunResult(null);

    try {
      const response = await fetch(`${BACKEND_URL}/api/ec2/run-repo`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          github_url: trimmedUrl,
          branch: repoBranch.trim() || 'main',
          setup_command: setupCommand.trim() || undefined,
          start_command: trimmedStart,
          stack_name: deployedTarget.stackName,
          region: deployedTarget.region,
        }),
      });

      const data = (await response.json()) as RepoRunApiResponse | { detail?: string };
      if (!response.ok) {
        const message = 'detail' in data && data.detail
          ? data.detail
          : `Repo run request failed with ${response.status}`;
        throw new Error(message);
      }

      setRepoRunResult(data as RepoRunApiResponse);
      if ((data as RepoRunApiResponse).status !== 'success') {
        throw new Error((data as RepoRunApiResponse).error || 'Repo execution did not finish successfully.');
      }
    } catch (error) {
      setFetchError(error instanceof Error ? error.message : 'Unable to run GitHub repo on EC2');
    } finally {
      setIsRunningRepo(false);
    }
  }

  async function copyOutput(text: string) {
    await navigator.clipboard.writeText(text);
    setCopyState('copied');
    window.setTimeout(() => setCopyState('idle'), 1400);
  }

  async function loadFile(file: File | null) {
    if (!file) {
      return;
    }
    const content = await file.text();
    setSourceText(content);
    setFetchError(null);
  }

  return (
    <section id="studio" className="py-10 sm:py-16 lg:py-20">
      <div className="grid gap-6 lg:grid-cols-[1.15fr_0.85fr]">
        <Card className="overflow-hidden border-white/10 bg-card/90 shadow-2xl shadow-black/20">
          <CardHeader className="border-b border-white/5 pb-4">
            <div className="flex flex-wrap items-center gap-3">
              <Badge variant="outline" className="rounded-full border-white/15 bg-white/5 px-3 py-1 text-[11px] uppercase tracking-[0.28em] text-muted-foreground">
                Migration studio
              </Badge>
              <Badge variant={statusTone(status) as 'default' | 'secondary' | 'destructive' | 'outline'} className="rounded-full">
                {formatLabel(status)}
              </Badge>
              {isPolling ? (
                <Badge variant="outline" className="rounded-full">
                  Syncing live job
                </Badge>
              ) : null}
            </div>
            <CardTitle className="font-display text-3xl text-foreground sm:text-4xl">
              Build migrations in one React workspace.
            </CardTitle>
            <CardDescription className="max-w-2xl text-base text-muted-foreground">
              Paste Terraform or GCP JSON, trigger the existing FastAPI backend, then inspect the generated AWS CloudFormation and runbook in one tab.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-5 pt-6">
            <div className="grid gap-3 sm:grid-cols-3">
              <Card className="border-white/10 bg-background/50 py-4">
                <CardContent className="px-4">
                  <p className="text-xs uppercase tracking-[0.24em] text-muted-foreground">Backend</p>
                  <p className="mt-1 text-sm font-medium text-foreground">{BACKEND_URL}</p>
                </CardContent>
              </Card>
              <Card className="border-white/10 bg-background/50 py-4">
                <CardContent className="px-4">
                  <p className="text-xs uppercase tracking-[0.24em] text-muted-foreground">Progress</p>
                  <p className="mt-1 text-sm font-medium text-foreground">{progress}%</p>
                </CardContent>
              </Card>
              <Card className="border-white/10 bg-background/50 py-4">
                <CardContent className="px-4">
                  <p className="text-xs uppercase tracking-[0.24em] text-muted-foreground">Job</p>
                  <p className="mt-1 truncate text-sm font-medium text-foreground">{job?.id ?? 'No job started yet'}</p>
                </CardContent>
              </Card>
            </div>

            <Tabs value={sampleKind} onValueChange={(value) => setSampleKind(value as SampleKind)} className="space-y-4">
              <TabsList className="grid w-full grid-cols-2 rounded-full bg-white/5 p-1">
                <TabsTrigger value="json" className="rounded-full">
                  <FileJson2 className="size-4" />
                  GCP JSON
                </TabsTrigger>
                <TabsTrigger value="terraform" className="rounded-full">
                  <FileCode2 className="size-4" />
                  Terraform
                </TabsTrigger>
              </TabsList>

              <TabsContent value="json" className="space-y-4">
                <Textarea
                  value={sourceText}
                  onChange={(event) => setSourceText(event.target.value)}
                  className="min-h-[420px] rounded-2xl border-white/10 bg-background/60 font-mono text-[13px] leading-6"
                  placeholder="Paste GCP JSON here"
                />
              </TabsContent>

              <TabsContent value="terraform" className="space-y-4">
                <Textarea
                  value={sourceText}
                  onChange={(event) => setSourceText(event.target.value)}
                  className="min-h-[420px] rounded-2xl border-white/10 bg-background/60 font-mono text-[13px] leading-6"
                  placeholder="Paste Terraform here"
                />
              </TabsContent>
            </Tabs>

            <div className="flex flex-wrap items-center gap-3">
              <Button onClick={submitMigration} disabled={isSubmitting} className="rounded-full px-5">
                {isSubmitting ? <Loader2 className="size-4 animate-spin" /> : <Play className="size-4" />}
                Run migration
              </Button>
              <Button
                onClick={deployStack}
                disabled={isDeploying || !cloudformation.trim()}
                variant="secondary"
                className="rounded-full px-5"
              >
                {isDeploying ? <Loader2 className="size-4 animate-spin" /> : <CloudUpload className="size-4" />}
                Deploy to AWS
              </Button>
              <Button
                variant="outline"
                className="rounded-full px-5"
                onClick={() => fileInputRef.current?.click()}
              >
                <Upload className="size-4" />
                Load file
              </Button>
              <Button
                variant="ghost"
                className="rounded-full px-5"
                onClick={() => setSourceText(sampleKind === 'json' ? SAMPLE_JSON : SAMPLE_TERRAFORM)}
              >
                <RefreshCcw className="size-4" />
                Reset sample
              </Button>
              <input
                ref={fileInputRef}
                type="file"
                accept=".json,.tf,.txt"
                className="hidden"
                onChange={(event) => loadFile(event.target.files?.[0] ?? null)}
              />
            </div>

            {fetchError ? (
              <div className="rounded-2xl border border-destructive/40 bg-destructive/10 px-4 py-3 text-sm text-destructive-foreground">
                {fetchError}
              </div>
            ) : null}

            {deploySummary ? (
              <div className="rounded-2xl border border-emerald-400/40 bg-emerald-400/10 px-4 py-3 text-sm text-emerald-100">
                {deploySummary}
              </div>
            ) : null}

            {deployLogs.length > 0 ? (
              <div className="space-y-2 rounded-2xl border border-white/10 bg-background/50 p-3">
                <p className="text-xs uppercase tracking-[0.2em] text-muted-foreground">Deploy logs</p>
                <ScrollArea className="h-[160px] rounded-xl border border-white/10 bg-background/70 p-3">
                  <div className="space-y-2 pr-3 font-mono text-xs leading-5 text-muted-foreground">
                    {deployLogs.map((line, index) => (
                      <p key={`${index}-${line}`}>{line}</p>
                    ))}
                  </div>
                </ScrollArea>
              </div>
            ) : null}

            {deploySummary ? (
              <div className="space-y-3 rounded-2xl border border-rose-300/30 bg-rose-300/10 p-4">
                <p className="text-xs uppercase tracking-[0.2em] text-rose-100">Run GitHub repo on deployed EC2</p>
                <div className="grid gap-3 sm:grid-cols-2">
                  <input
                    value={githubUrl}
                    onChange={(event) => setGithubUrl(event.target.value)}
                    placeholder="https://github.com/owner/repo"
                    className="rounded-xl border border-white/15 bg-background/60 px-3 py-2 text-sm text-foreground outline-none ring-0 placeholder:text-muted-foreground"
                  />
                  <input
                    value={repoBranch}
                    onChange={(event) => setRepoBranch(event.target.value)}
                    placeholder="main"
                    className="rounded-xl border border-white/15 bg-background/60 px-3 py-2 text-sm text-foreground outline-none ring-0 placeholder:text-muted-foreground"
                  />
                  <input
                    value={setupCommand}
                    onChange={(event) => setSetupCommand(event.target.value)}
                    placeholder="Optional setup command (e.g. npm install)"
                    className="rounded-xl border border-white/15 bg-background/60 px-3 py-2 text-sm text-foreground outline-none ring-0 placeholder:text-muted-foreground sm:col-span-2"
                  />
                  <input
                    value={startCommand}
                    onChange={(event) => setStartCommand(event.target.value)}
                    placeholder="Start command (e.g. npm start)"
                    className="rounded-xl border border-white/15 bg-background/60 px-3 py-2 text-sm text-foreground outline-none ring-0 placeholder:text-muted-foreground sm:col-span-2"
                  />
                </div>
                <div className="flex items-center gap-2">
                  <Button
                    onClick={runGithubRepoOnEc2}
                    disabled={isRunningRepo}
                    className="rounded-full bg-rose-200 text-black hover:bg-rose-100"
                  >
                    {isRunningRepo ? <Loader2 className="size-4 animate-spin" /> : <ArrowRight className="size-4" />}
                    Run repo on EC2
                  </Button>
                  {repoRunResult?.status ? (
                    <Badge variant={repoRunResult.status === 'success' ? 'secondary' : 'destructive'} className="rounded-full">
                      Repo run: {repoRunResult.status}
                    </Badge>
                  ) : null}
                </div>

                {repoRunResult ? (
                  <div className="space-y-2 rounded-xl border border-white/15 bg-background/50 p-3">
                    <p className="text-xs uppercase tracking-[0.2em] text-muted-foreground">
                      Instance {repoRunResult.instance_id} • {repoRunResult.app_dir}
                    </p>
                    <ScrollArea className="h-[140px] rounded-xl border border-white/10 bg-background/70 p-3">
                      <pre className="whitespace-pre-wrap pr-3 font-mono text-xs leading-5 text-muted-foreground">
                        {repoRunResult.stdout || repoRunResult.stderr || repoRunResult.error || 'No output returned yet.'}
                      </pre>
                    </ScrollArea>
                  </div>
                ) : null}
              </div>
            ) : null}
          </CardContent>
        </Card>

        <div className="space-y-6">
          <Card className="border-white/10 bg-card/90 shadow-xl shadow-black/15">
            <CardHeader className="border-b border-white/5 pb-4">
              <CardTitle className="text-xl text-foreground">Job console</CardTitle>
              <CardDescription>Track the backend job as it moves through planner, critic, refiner, and runbook.</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4 pt-5">
              <div className="flex items-center justify-between gap-3 text-sm text-muted-foreground">
                <span>Status</span>
                <span className="font-medium text-foreground">{formatLabel(status)}</span>
              </div>
              <div className="h-2 overflow-hidden rounded-full bg-white/8">
                <div
                  className="h-full rounded-full bg-gradient-to-r from-white via-slate-300 to-slate-500 transition-all duration-300"
                  style={{ width: `${progress}%` }}
                />
              </div>
              <div className="flex items-center justify-between text-sm text-muted-foreground">
                <span>Progress</span>
                <span className="font-medium text-foreground">{progress}%</span>
              </div>
              <Separator className="bg-white/8" />
              <ScrollArea className="h-[240px] rounded-2xl border border-white/10 bg-background/60 p-4">
                <div className="space-y-2 pr-4 font-mono text-xs leading-5 text-muted-foreground">
                  {(job?.logs?.length ? job.logs : ['Waiting for a job to start...']).map((line, index) => (
                    <p key={`${index}-${line}`}>{line}</p>
                  ))}
                </div>
              </ScrollArea>
              {job?.error ? (
                <div className="rounded-2xl border border-destructive/40 bg-destructive/10 px-4 py-3 text-sm text-destructive-foreground">
                  {job.error}
                </div>
              ) : null}
            </CardContent>
          </Card>

          <Card className="border-white/10 bg-card/90 shadow-xl shadow-black/15">
            <CardHeader className="border-b border-white/5 pb-4">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <CardTitle className="text-xl text-foreground">Output</CardTitle>
                  <CardDescription>CloudFormation and runbook from the backend response.</CardDescription>
                </div>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => copyOutput(cloudformation || runbook || sourceText)}
                  className="rounded-full"
                >
                  <Copy className="size-4" />
                  {copyState === 'copied' ? 'Copied' : 'Copy'}
                </Button>
              </div>
            </CardHeader>
            <CardContent className="pt-5">
              <Tabs defaultValue="cloudformation" className="space-y-4">
                <TabsList className="grid w-full grid-cols-2 rounded-full bg-white/5 p-1">
                  <TabsTrigger value="cloudformation" className="rounded-full">
                    CloudFormation
                  </TabsTrigger>
                  <TabsTrigger value="runbook" className="rounded-full">
                    Runbook
                  </TabsTrigger>
                </TabsList>
                <TabsContent value="cloudformation" className="mt-0 space-y-3">
                  <ScrollArea className="h-[260px] rounded-2xl border border-white/10 bg-background/60">
                    <pre className="whitespace-pre-wrap p-4 font-mono text-[12px] leading-5 text-foreground">
                      {cloudformation || 'Run a job to see generated YAML here.'}
                    </pre>
                  </ScrollArea>
                </TabsContent>
                <TabsContent value="runbook" className="mt-0 space-y-3">
                  <ScrollArea className="h-[260px] rounded-2xl border border-white/10 bg-background/60">
                    <pre className="whitespace-pre-wrap p-4 font-mono text-[12px] leading-5 text-foreground">
                      {runbook || 'Run a job to see the migration runbook here.'}
                    </pre>
                  </ScrollArea>
                </TabsContent>
              </Tabs>
            </CardContent>
          </Card>

        </div>
      </div>

      {cloudformation && sourceText ? (
        <div className="mt-10">
          <ArchitectureDiagrams
            sourceJson={sourceText}
            cloudformationYaml={cloudformation}
            isLoading={status === 'running'}
          />
        </div>
      ) : null}
    </section>
  );
}
