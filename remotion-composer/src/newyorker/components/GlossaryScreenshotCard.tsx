import React from 'react';
import {AbsoluteFill} from 'remotion';
import {CN_FONT, INK, MUTED} from '../theme';
import {F} from '../fonts';
import type {Episode} from '../episode';

export const GlossaryScreenshotCard: React.FC<{episode: Episode}> = ({
  episode,
}) => {
  return (
    <AbsoluteFill
      style={{
        padding: '120px 96px 100px',
        display: 'flex',
        flexDirection: 'column',
      }}
    >
      <div
        style={{
          textAlign: 'center',
          fontFamily: F.sans,
          fontSize: 16,
          letterSpacing: 6.5,
          color: INK,
        }}
      >
        SCREENSHOT TO SAVE
      </div>

      <div
        style={{
          flex: 1,
          display: 'flex',
          flexDirection: 'column',
          justifyContent: 'space-evenly',
          marginTop: 24,
        }}
      >
        {episode.glossaryCard.map((item) => (
          <div key={item.phrase}>
            <div
              style={{
                fontFamily: F.display,
                fontSize: 50,
                fontWeight: 700,
                lineHeight: 1.12,
                letterSpacing: -0.5,
              }}
            >
              {item.phrase}
            </div>
            <div
              style={{
                marginTop: 8,
                fontFamily: F.sans,
                fontSize: 19,
                letterSpacing: 0.3,
                color: MUTED,
              }}
            >
              {item.phonetic}
            </div>
            <div
              style={{
                marginTop: 8,
                fontFamily: CN_FONT,
                fontSize: 27,
                lineHeight: 1.4,
                color: INK,
              }}
            >
              {item.meaning}
            </div>
          </div>
        ))}
      </div>
    </AbsoluteFill>
  );
};
