import type { FileInfo } from '@/api/file';

const CAD_KEYWORDS = [
  'cad',
  'dxf',
  'dwg',
  'step',
  'stp',
  'mechanical drawing',
  '2d drawing',
  'technical drawing',
  'mounting plate',
  'base plate',
  'hole pattern',
  'bolt pattern',
  'centerline',
  'dimension',
  'draw a plate',
  'drawing',
  'plate',
  'hole',
  'slot',
  'fillet',
  'radius',
  '画图',
  '制图',
  '出图',
  '画一个',
  '画一张',
  '生成图纸',
  '机械图',
  '工程图',
  '二维图',
  '零件图',
  '装配图',
  '安装板',
  '底板',
  '板',
  '孔位',
  '开孔',
  '螺丝孔',
  '螺栓孔',
  '通孔',
  '沉孔',
  '槽',
  '长圆孔',
  '倒角',
  '圆角',
  '标注',
  '尺寸',
];

const CAD_FILE_EXTENSIONS = [
  '.dxf',
  '.dwg',
  '.step',
  '.stp',
  '.iges',
  '.igs',
  '.stl',
];

const SIZE_PATTERN = /\b\d+(?:\.\d+)?\s*(?:x|×|\*)\s*\d+(?:\.\d+)?(?:\s*(?:x|×|\*)\s*\d+(?:\.\d+)?)?\b/i;
const METRIC_HOLE_PATTERN = /\bm\s*\d+(?:\.\d+)?\b/i;

function hasCADFile(files: FileInfo[]): boolean {
  return files.some(file => {
    const filename = file.filename.toLowerCase();
    return CAD_FILE_EXTENSIONS.some(extension => filename.endsWith(extension));
  });
}

function hasMechanicalShape(text: string): boolean {
  const lowerText = text.toLowerCase();
  const hasSize = SIZE_PATTERN.test(lowerText);
  const hasFeature = [
    'hole',
    'slot',
    'fillet',
    'radius',
    '孔',
    '槽',
    '圆角',
    '倒角',
    '安装板',
    '底板',
  ].some(keyword => lowerText.includes(keyword));

  return hasSize && (hasFeature || METRIC_HOLE_PATTERN.test(lowerText));
}

export function isCADIntent(message: string, files: FileInfo[] = []): boolean {
  const text = message.trim();
  const lowerText = text.toLowerCase();

  if (hasCADFile(files)) return true;
  if (CAD_KEYWORDS.some(keyword => lowerText.includes(keyword.toLowerCase()))) return true;
  return hasMechanicalShape(text);
}
