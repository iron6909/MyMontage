import React from 'react';
import {
  AbsoluteFill,
  Img,
  interpolate,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from 'remotion';
import type {Episode, Scene} from '../episode';

type Props = {
  episode: Episode;
  scene: Scene;
  previousScene?: Scene | null;
  sceneIndex: number;
  fromFrame: number;
  durationInFrames: number;
};

const DISSOLVE_SEC = 0.3;
const PAGE_SEC = 0.5;

const easeInOut = (t: number): number =>
  t < 0.5 ? 4 * t * t * t : 1 - (-2 * t + 2) ** 3 / 2;

export const BoilingFrame: React.FC<Props> = ({
  episode,
  scene,
  previousScene,
  sceneIndex,
  fromFrame,
  durationInFrames,
}: Props) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const local = Math.max(0, frame - fromFrame);
  const duration = Math.max(1, durationInFrames);
  // dissolve_030 | push_035 | slideup_050. Full-colour frames use normal
  // blending; the previous page unmounts the moment its transition ends, so
  // only one page is ever on screen (zero double exposure).
  const transition = scene.transition ?? 'dissolve_030';
  const mode = transition === 'crossfade_035' || transition === 'dissolve_035' ? 'dissolve_030' : transition;
  const transSec = mode === 'slideup_050' ? PAGE_SEC : DISSOLVE_SEC;
  const transFrames = Math.max(1, Math.round(fps * transSec));
  const progress = previousScene
    ? easeInOut(Math.min(1, local / transFrames))
    : 1;

  const direction = sceneIndex % 2 === 0 ? -1 : 1;
  const scale = interpolate(
    local,
    [0, duration],
    direction === 1 ? [1.0, 1.06] : [1.06, 1.0],
    {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'},
  );
  const panX = interpolate(
    local,
    [0, duration],
    direction === 1 ? [-10, 10] : [10, -10],
    {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'},
  );

  const isDissolve = mode === 'dissolve_030';
  let fadeIn = 1;
  let fadeOut = 1;
  // Unmount the previous page as soon as the transition completes.
  const showPrevious = Boolean(previousScene) && local < transFrames;
  if (previousScene && isDissolve) {
    fadeIn = interpolate(local, [0, transFrames], [0, 1], {
      extrapolateLeft: 'clamp',
      extrapolateRight: 'clamp',
    });
    fadeOut = 1 - fadeIn;
  }
  const pushDir = sceneIndex % 2 === 0 ? 1 : -1;
  const pushX =
    previousScene && mode === 'push_035' ? (1 - progress) * 100 * pushDir : 0;
  const pullX =
    previousScene && mode === 'push_035' ? progress * -6 * pushDir : 0;
  const slideY =
    previousScene && mode === 'slideup_050' ? (1 - progress) * 100 : 0;
  const incomingScale =
    previousScene && mode === 'slideup_050'
      ? 1.04 - 0.04 * progress
      : scale;
  const previousDirection = (sceneIndex - 1) % 2 === 0 ? -1 : 1;
  const previousSettledScale = previousDirection === 1 ? 1.06 : 1.0;
  const outgoingScale =
    previousScene && mode === 'slideup_050'
      ? previousSettledScale * (1 - 0.015 * progress)
      : previousSettledScale;

  const currentImage = staticFile(
    `frames/${episode.epId}/${scene.id}_00.png`,
  );
  const previousImage = previousScene
    ? staticFile(`frames/${episode.epId}/${previousScene.id}_00.png`)
    : null;

  return (
    <AbsoluteFill style={{overflow: 'hidden'}}>
      {showPrevious && previousImage ? (
        <Img
          src={previousImage}
          style={{
            position: 'absolute',
            inset: 0,
            width: '100%',
            height: '100%',
            objectFit: 'cover',
            opacity: fadeOut,
            transform: `scale(${outgoingScale}) translateX(${pullX}px)`,
          }}
        />
      ) : null}
      <Img
        src={currentImage}
        style={{
          position: 'absolute',
          inset: 0,
          width: '100%',
          height: '100%',
          objectFit: 'cover',
          opacity: fadeIn,
          transform: `scale(${incomingScale}) translateX(calc(${panX}px + ${pushX}%)) translateY(${slideY}%)`,
        }}
      />
    </AbsoluteFill>
  );
};
