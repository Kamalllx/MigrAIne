'use client';

import * as React from 'react';
import mermaidLib from 'mermaid';
import { AlertCircle, Loader2 } from 'lucide-react';

import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';

interface ArchitectureDiagramsProps {
  sourceJson: string;
  cloudformationYaml: string;
  isLoading?: boolean;
}

export function ArchitectureDiagrams({
  sourceJson,
  cloudformationYaml,
  isLoading = false,
}: ArchitectureDiagramsProps) {
  const [diagrams, setDiagrams] = React.useState<{
    gcp_diagram: string;
    aws_diagram: string;
    mapping_diagram: string;
    summary: {
      gcp_count: number;
      aws_count: number;
      added_count: number;
      gcp_edge_count: number;
      aws_edge_count: number;
    };
  } | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [isGenerating, setIsGenerating] = React.useState(false);

  React.useEffect(() => {
    if (!sourceJson || !cloudformationYaml || isLoading) {
      return;
    }

    const generateDiagrams = async () => {
      setIsGenerating(true);
      setError(null);

      try {
        const backendUrl = process.env.NEXT_PUBLIC_BACKEND_URL ?? 'http://127.0.0.1:8000';
        const response = await fetch(`${backendUrl}/api/diagrams`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({
            source_json: sourceJson,
            cloudformation_yaml: cloudformationYaml,
          }),
        });

        if (!response.ok) {
          throw new Error(`Failed to generate diagrams: ${response.statusText}`);
        }

        const data = await response.json();
        setDiagrams(data);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to generate diagrams');
      } finally {
        setIsGenerating(false);
      }
    };

    generateDiagrams();
  }, [sourceJson, cloudformationYaml, isLoading]);

  if (isLoading || isGenerating) {
    return (
      <Card className="border-white/10 bg-card/90">
        <CardHeader>
          <CardTitle>Architecture Diagrams</CardTitle>
        </CardHeader>
        <CardContent className="flex items-center justify-center py-12">
          <Loader2 className="h-6 w-6 animate-spin" />
          <span className="ml-2">Generating diagrams...</span>
        </CardContent>
      </Card>
    );
  }

  if (error) {
    return (
      <Card className="border-white/10 bg-card/90">
        <CardHeader>
          <CardTitle>Architecture Diagrams</CardTitle>
        </CardHeader>
        <CardContent>
          <Alert variant="destructive">
            <AlertCircle className="h-4 w-4" />
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        </CardContent>
      </Card>
    );
  }

  if (!diagrams) {
    return null;
  }

  return (
    <Card className="border-white/10 bg-card/90 shadow-xl shadow-black/15">
      <CardHeader className="border-b border-white/5 pb-4">
        <CardTitle>Architecture Diagrams</CardTitle>
        <CardDescription>
          Visual representation of GCP source, AWS target, and resource mappings
        </CardDescription>
      </CardHeader>
      <CardContent className="pt-6">
        <div className="mb-4 grid gap-3 sm:grid-cols-3">
          <Card className="border-pink-300/40 bg-pink-950/30 py-3">
            <CardContent className="px-4">
              <p className="text-xs uppercase tracking-[0.2em] text-pink-200/80">GCP Resources</p>
              <p className="mt-1 text-base font-semibold text-pink-100">{diagrams.summary.gcp_count}</p>
            </CardContent>
          </Card>
          <Card className="border-pink-300/40 bg-pink-950/30 py-3">
            <CardContent className="px-4">
              <p className="text-xs uppercase tracking-[0.2em] text-pink-200/80">AWS Resources</p>
              <p className="mt-1 text-base font-semibold text-pink-100">{diagrams.summary.aws_count}</p>
            </CardContent>
          </Card>
          <Card className="border-pink-300/40 bg-pink-950/30 py-3">
            <CardContent className="px-4">
              <p className="text-xs uppercase tracking-[0.2em] text-pink-200/80">Added By Transformation</p>
              <p className="mt-1 text-base font-semibold text-pink-100">{diagrams.summary.added_count}</p>
            </CardContent>
          </Card>
        </div>

        <div className="mb-5 rounded-xl border border-pink-300/40 bg-pink-950/20 px-4 py-3 text-sm text-pink-100/90">
          Relationships detected: GCP {diagrams.summary.gcp_edge_count} edge(s), AWS {diagrams.summary.aws_edge_count} edge(s).
        </div>

        <Tabs defaultValue="gcp" className="w-full">
          <TabsList className="grid w-full grid-cols-3 rounded-full bg-white/5 p-1">
            <TabsTrigger value="gcp" className="rounded-full">GCP Architecture</TabsTrigger>
            <TabsTrigger value="aws" className="rounded-full">AWS Architecture</TabsTrigger>
            <TabsTrigger value="mapping" className="rounded-full">Migration Mapping</TabsTrigger>
          </TabsList>

          <TabsContent value="gcp" className="mt-4 space-y-3">
            <MermaidDiagram mermaid={diagrams.gcp_diagram} />
          </TabsContent>

          <TabsContent value="aws" className="mt-4 space-y-3">
            <MermaidDiagram mermaid={diagrams.aws_diagram} />
          </TabsContent>

          <TabsContent value="mapping" className="mt-4 space-y-3">
            <MermaidDiagram mermaid={diagrams.mapping_diagram} />
          </TabsContent>
        </Tabs>
      </CardContent>
    </Card>
  );
}

interface MermaidDiagramProps {
  mermaid: string;
}

function MermaidDiagram({ mermaid }: MermaidDiagramProps) {
  const svgRef = React.useRef<HTMLDivElement>(null);

  React.useEffect(() => {
    let cancelled = false;

    const renderDiagram = async () => {
      if (!svgRef.current || !mermaid.trim()) {
        return;
      }

      try {
        mermaidLib.initialize({
          startOnLoad: false,
          securityLevel: 'loose',
          theme: 'dark',
        });

        const diagramId = `diagram-${Math.random().toString(36).slice(2, 9)}`;
        const { svg } = await mermaidLib.render(diagramId, mermaid);

        if (!cancelled && svgRef.current) {
          svgRef.current.innerHTML = svg;
        }
      } catch (error) {
        console.error('Mermaid rendering error:', error);
        if (!cancelled && svgRef.current) {
          svgRef.current.innerHTML = `<div class="rounded-lg border border-destructive/40 bg-destructive/10 p-4 text-sm text-destructive-foreground">
            <p>Failed to render diagram</p>
            <p class="text-xs mt-2">${error instanceof Error ? error.message : 'Unknown mermaid error'}</p>
          </div>`;
        }
      }
    };

    renderDiagram();

    return () => {
      cancelled = true;
    };
  }, [mermaid]);

  return (
    <div
      ref={svgRef}
      className="flex justify-center rounded-lg border border-white/10 bg-background/60 p-4 overflow-auto"
      style={{ minHeight: '400px' }}
    >
      <div className="text-sm text-muted-foreground">Loading diagram...</div>
    </div>
  );
}
