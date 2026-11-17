import React from 'react';
import {AbsoluteFill} from 'remotion';
import {PAPER, paperTexture} from '../theme';

type Props = {children: React.ReactNode};

export const PaperCanvas: React.FC<Props> = ({children}: Props) => {
  return (
    <AbsoluteFill
      style={{
        backgroundColor: PAPER,
        backgroundImage: paperTexture,
        overflow: 'hidden',
      }}
    >
      {children}
    </AbsoluteFill>
  );
};
