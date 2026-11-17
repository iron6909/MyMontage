import React from 'react';
import {useVideoConfig} from 'remotion';
import {CN_FONT} from '../theme';
import {F} from '../fonts';
import type {Episode} from '../episode';

// Solid subtitle card (opaque, no backdrop interference).
const V5_UNREAD = '#4A453E';
const V5_READ = '#141210';
const V5_CURRENT = '#C24E12';
const V5_CN = '#3D3832';
// Sentences this short render merged with the previous sentence so a tiny
// card never flashes on screen alone.
const TAIL_MERGE_WORDS = 8;
const TAIL_MERGE_GAP_SEC = 1.5;
// Keep showing the finished sentence through short gaps instead of popping
// the next one in early.
const GAP_HOLD_SEC = 0.3;

type Props = {
  episode: Episode;
  frame: number;
};

const wordColor = (isCurrent: boolean, isRead: boolean): string => {
  if (isCurrent) {
    return V5_CURRENT;
  }
  if (isRead) {
    return V5_READ;
  }
  return V5_UNREAD;
};

export const KaraokeSubtitles: React.FC<Props> = ({episode, frame}) => {
  const {fps} = useVideoConfig();
  const t = frame / fps;

  let sentenceIndex = episode.sentences.findIndex((s) => t < s.endSec);
  if (sentenceIndex === -1) {
    sentenceIndex = episode.sentences.length - 1;
  }
  // Gap hold: during a short pause after a sentence ends, keep it on screen.
  if (sentenceIndex > 0) {
    const prev = episode.sentences[sentenceIndex - 1];
    const next = episode.sentences[sentenceIndex];
    if (t >= prev.endSec && t < next.startSec - GAP_HOLD_SEC) {
      sentenceIndex -= 1;
    }
  }
  const sentence = episode.sentences[sentenceIndex];
  // Tail merge: a very short sentence shares the card with its predecessor.
  const prev =
    sentenceIndex > 0 ? episode.sentences[sentenceIndex - 1] : null;
  const merged =
    prev !== null &&
    sentence.words.length <= TAIL_MERGE_WORDS &&
    sentence.startSec - prev.endSec < TAIL_MERGE_GAP_SEC
      ? prev
      : null;

  const currentWord = sentence.words.findIndex(
    (w) => t >= w.start && t <= w.end + 0.1,
  );
  const lastRead = sentence.words.reduce(
    (last, word, index) => (t >= word.start ? index : last),
    -1,
  );
  const activeWord = currentWord >= 0 ? currentWord : lastRead;

  const renderWords = (
    words: typeof sentence.words,
    opts: {allRead: boolean; current: number},
  ) => (
    <>
      {words.map((word, index) => {
        const isRead = opts.allRead || t >= word.start;
        const isCurrent = !opts.allRead && index === opts.current;
        return (
          <span
            key={`${word.word}-${word.start}`}
            style={{color: wordColor(isCurrent, isRead)}}
          >
            {word.word}
          </span>
        );
      })}
    </>
  );

  return (
    <div
      style={{
        position: 'absolute',
        left: 64,
        right: 64,
        bottom: 180,
        display: 'flex',
        justifyContent: 'center',
        pointerEvents: 'none',
      }}
    >
      <div
        style={{
          backgroundColor: 'rgba(249, 246, 240, 0.95)',
          border: '1px solid rgba(40, 35, 30, 0.15)',
          borderRadius: 24,
          padding: '28px 36px',
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          maxWidth: 952,
        }}
      >
        {merged ? (
          <div
            style={{
              display: 'flex',
              flexWrap: 'wrap',
              justifyContent: 'center',
              alignItems: 'baseline',
              columnGap: '0.27em',
              rowGap: '0.12em',
              textAlign: 'center',
              fontFamily: F.serif,
              fontSize: 44,
              fontWeight: 600,
              lineHeight: 1.32,
              letterSpacing: -0.4,
              marginBottom: 14,
            }}
          >
            {renderWords(merged.words, {allRead: true, current: -1})}
          </div>
        ) : null}
        <div
          style={{
            display: 'flex',
            flexWrap: 'wrap',
            justifyContent: 'center',
            alignItems: 'baseline',
            columnGap: '0.27em',
            rowGap: '0.12em',
            textAlign: 'center',
            fontFamily: F.serif,
            fontSize: 60,
            fontWeight: 600,
            lineHeight: 1.32,
            letterSpacing: -0.4,
          }}
        >
          {renderWords(sentence.words, {allRead: false, current: activeWord})}
        </div>
        {episode.showCnSubtitle ? (
          <div
            style={{
              marginTop: 18,
              fontFamily: CN_FONT,
              fontSize: 36,
              fontWeight: 500,
              lineHeight: 1.6,
              letterSpacing: 0.5,
              color: V5_CN,
              textAlign: 'center',
              maxWidth: 880,
            }}
          >
            {sentence.textCn}
          </div>
        ) : null}
      </div>
    </div>
  );
};
