import type { FileInfo } from '@/api/file';

const CAD_KEYWORDS = [
  'cad',
  'dxf',
  'dwg',
  'mechanical drawing',
  '2d drawing',
  'technical drawing',
  'mounting plate',
  'base plate',
  'hole pattern',
  'bolt pattern',
  'slot',
  'centerline',
  'dimension',
  'draw a plate',
  '画图',
  '制图',
  '机械图',
  '工程图',
  '二维图',
  '零件图',
  '装配图',
  '孔位',
  '开孔',
  '槽',
  '倒角',
  '圆角',
  '标注',
  '渲染',
];

const CAD_FILE_EXTENSIONS = ['.dxf', '.dwg', '.step', '.stp', '.iges', '.igs', '.stl'];

export function isCADIntent(message: string, files: FileInfo[] = []): boolean {
  const text = message.toLowerCase();
  const hasCadKeyword = CAD_KEYWORDS.some(keyword => text.includes(keyword.toLowerCase()));
  if (hasCadKeyword) return true;

  return files.some(file => {
    const filename = file.filename.toLowerCase();
    return CAD_FILE_EXTENSIONS.some(extension => filename.endsWith(extension));
  });
}
