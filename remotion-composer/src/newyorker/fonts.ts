import {loadFont as loadInter} from '@remotion/google-fonts/Inter';
import {loadFont as loadLibre} from '@remotion/google-fonts/LibreBaskerville';
import {loadFont as loadPlayfair} from '@remotion/google-fonts/PlayfairDisplay';

const playfair = loadPlayfair('normal', {
  weights: ['400', '700', '900'],
  subsets: ['latin'],
});

const libre = loadLibre('normal', {
  weights: ['400', '700'],
  subsets: ['latin'],
});

const inter = loadInter('normal', {
  weights: ['400', '500', '600'],
  subsets: ['latin'],
});

export const F = {
  display: playfair.fontFamily,
  serif: libre.fontFamily,
  sans: inter.fontFamily,
} as const;
