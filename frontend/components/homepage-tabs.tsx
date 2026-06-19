'use client';

import * as React from 'react';

import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';

type HomepageTabsProps = {
  landing: React.ReactNode;
  studio: React.ReactNode;
};

export function HomepageTabs({ landing, studio }: HomepageTabsProps) {
  const [activeTab, setActiveTab] = React.useState<'landing' | 'studio'>(() => {
    if (typeof window === 'undefined') {
      return 'landing';
    }

    return window.location.hash === '#studio' ? 'studio' : 'landing';
  });

  React.useEffect(() => {
    const syncFromHash = () => {
      setActiveTab(window.location.hash === '#studio' ? 'studio' : 'landing');
    };

    syncFromHash();
    window.addEventListener('hashchange', syncFromHash);

    return () => window.removeEventListener('hashchange', syncFromHash);
  }, []);

  return (
    <section className="relative">
      <Tabs value={activeTab} onValueChange={(value) => setActiveTab(value as 'landing' | 'studio')} className="space-y-6">
        <div className="sticky top-0 z-40 border-b border-white/10 bg-background/80 backdrop-blur-xl">
          <div className="mx-auto flex max-w-[1400px] flex-col gap-4 px-4 py-4 sm:px-6 lg:flex-row lg:items-center lg:justify-between lg:px-8">
            <div>
              <p className="text-xs uppercase tracking-[0.32em] text-muted-foreground">
                MigrAI
              </p>
              <p className="font-display text-lg text-foreground sm:text-xl">
                Landing + migration workspace
              </p>
            </div>
            <TabsList className="grid w-full max-w-md grid-cols-2 rounded-full bg-white/5 p-1">
              <TabsTrigger value="landing" className="rounded-full">
                Landing
              </TabsTrigger>
              <TabsTrigger value="studio" className="rounded-full">
                Migration studio
              </TabsTrigger>
            </TabsList>
          </div>
        </div>

        <div className="mx-auto max-w-[1400px] px-4 sm:px-6 lg:px-8">
          <TabsContent value="landing" className="mt-0">
            {landing}
          </TabsContent>
          <TabsContent value="studio" className="mt-0">
            {studio}
          </TabsContent>
        </div>
      </Tabs>
    </section>
  );
}
