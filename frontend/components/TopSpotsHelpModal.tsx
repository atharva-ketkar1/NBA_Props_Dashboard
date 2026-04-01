import React from 'react';
import { X } from 'lucide-react';

interface TopSpotsHelpModalProps {
  onClose: () => void;
}

const Section = ({ title, children }: { title: string; children: React.ReactNode }) => (
  <div className="mb-7">
    <h2 className="mb-3 border-b border-white/10 pb-2 text-[15px] font-bold text-white">{title}</h2>
    {children}
  </div>
);

const ExampleBox = ({ children }: { children: React.ReactNode }) => (
  <div className="my-3 rounded-lg border border-borderMedium/40 bg-bgElevation2 px-4 py-3 text-[12px] leading-relaxed text-neutral300">
    {children}
  </div>
);

const Label = ({ color = 'text-neutral300', children }: { color?: string; children: React.ReactNode }) => (
  <span className={`font-bold ${color}`}>{children}</span>
);

export const TopSpotsHelpModal: React.FC<TopSpotsHelpModalProps> = ({ onClose }) => {
  return (
    <div
      className="fixed inset-0 z-[200] flex items-center justify-center"
      style={{ background: 'rgba(0,0,0,0.75)', backdropFilter: 'blur(4px)' }}
      onClick={onClose}
    >
      <div
        className="relative mx-4 flex max-h-[85vh] w-full max-w-[560px] flex-col rounded-2xl border border-borderMedium/60 bg-bgElevation1 shadow-2xl"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="flex shrink-0 items-center justify-between border-b border-white/8 px-6 pb-4 pt-5">
          <h1 className="text-[17px] font-bold tracking-tight text-white">How Top Spots Works</h1>
          <button
            onClick={onClose}
            className="rounded-lg p-1 text-fgSubtle transition-colors hover:bg-white/10 hover:text-white"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="overflow-y-auto px-6 py-5 text-[13px] leading-relaxed text-neutral400">
          <Section title="What This Board Is">
            <p>
              <Label color="text-white">Top Spots</Label> is the global scouting board for today&apos;s slate. It is meant to help you find the strongest current prop spots quickly before you drill into a player&apos;s full dashboard.
            </p>
            <ExampleBox>
              <div className="space-y-1">
                <div><Label color="text-white">Global board</Label> — not tied to the one player you currently have open.</div>
                <div><Label color="text-white">Live slate</Label> — built from the current prop feed and refreshed with the backend scoring pipeline.</div>
                <div><Label color="text-white">Book filter</Label> — lets you view the best current spots for one sportsbook at a time.</div>
              </div>
            </ExampleBox>
          </Section>

          <Section title="What Signal Score Means">
            <p>
              <Label color="text-white">Signal Score</Label> is our ranking number from <Label color="text-white">1 to 99</Label>. Higher scores mean more of the available data is lining up in the same direction on the current line.
            </p>
            <p className="mt-2 text-[12px]">
              It is <Label color="text-red500">not</Label> a hit probability, and it is <Label color="text-red500">not</Label> EV. It is a quick way to compare how strong one spot looks versus the rest of the board.
            </p>
          </Section>

          <Section title="What Goes Into The Ranking">
            <ExampleBox>
              <div className="space-y-1">
                <div><Label color="text-white">Projection gap</Label> — how the player&apos;s baseline compares to the posted line.</div>
                <div><Label color="text-white">Recent form</Label> — recent averages and how often the player has been going over or under similar numbers.</div>
                <div><Label color="text-white">Matchup context</Label> — things like shooting zones, assist lanes, shot type, play type, and opponent tendencies.</div>
                <div><Label color="text-white">Market value</Label> — whether this book is giving a better number than the rest of the market.</div>
                <div><Label color="text-white">Extra context</Label> — line movement where available, similar players, rest, and opponent history.</div>
              </div>
            </ExampleBox>
          </Section>

          <Section title="What Data Support Means">
            <p>
              <Label color="text-white">Data support</Label> tells you how much of the scoring model had usable input for that pick.
            </p>
            <ExampleBox>
              <div className="space-y-1">
                <div><Label color="text-white">84% data support</Label> means most of the scoring buckets had real data behind them.</div>
                <div><Label color="text-white">Lower support</Label> does not mean the pick is bad. It means fewer signal buckets were available for that specific prop.</div>
              </div>
            </ExampleBox>
            <p className="text-[12px]">
              This is about <Label color="text-white">how much information was available</Label>, not how likely the pick is to cash.
            </p>
          </Section>

          <Section title="How To Use It">
            <ExampleBox>
              <div className="space-y-1">
                <div><Label color="text-white">1.</Label> Scan the list for strong scores and lines you like.</div>
                <div><Label color="text-white">2.</Label> Filter by sportsbook if you want the best current spots from one book.</div>
                <div><Label color="text-white">3.</Label> Click a row to see the reasoning right underneath it.</div>
                <div><Label color="text-white">4.</Label> Use <Label color="text-white">Open</Label> to jump straight into that player in the main dashboard.</div>
              </div>
            </ExampleBox>
          </Section>
        </div>

        <div className="flex shrink-0 justify-end border-t border-white/8 px-6 py-3">
          <button
            onClick={onClose}
            className="rounded-lg border border-borderMedium bg-bgElevation2 px-4 py-1.5 text-[12px] font-medium text-white transition-colors hover:bg-bgElevation0"
          >
            Got it
          </button>
        </div>
      </div>
    </div>
  );
};
