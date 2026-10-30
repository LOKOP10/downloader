import 'package:flutter/material.dart';
import 'package:vdl/design/colors.dart';
import 'package:vdl/design/widgets/app_card.dart';
import 'package:vdl/design/widgets/platform_style.dart';
import 'package:vdl/features/parse/parser_registry.dart';
import 'package:vdl/shared/site.dart';

/// 平台宫格：列出全部站点，已支持解析的右上角带绿点。
///
/// 放在设置页底部（首页只留输入与结果，避免一进来就被九宫格占满）。
class PlatformGrid extends StatelessWidget {
  const PlatformGrid({super.key, required this.onTap});

  final void Function(SitePlatform platform) onTap;

  @override
  Widget build(BuildContext context) {
    return AppCard(
      padding: const EdgeInsets.symmetric(vertical: 16, horizontal: 12),
      child: GridView.count(
        crossAxisCount: 4,
        shrinkWrap: true,
        physics: const NeverScrollableScrollPhysics(),
        mainAxisSpacing: 14,
        childAspectRatio: 0.9,
        children: [
          for (final p in SitePlatform.values)
            _PlatformCell(
              platform: p,
              ready: ParserRegistry.supports(p),
              onTap: () => onTap(p),
            ),
        ],
      ),
    );
  }
}

class _PlatformCell extends StatelessWidget {
  const _PlatformCell({
    required this.platform,
    required this.onTap,
    required this.ready,
  });

  final SitePlatform platform;
  final VoidCallback onTap;
  final bool ready;

  @override
  Widget build(BuildContext context) {
    final palette = AppPalette.of(context);
    return InkWell(
      onTap: onTap,
      borderRadius: BorderRadius.circular(12),
      child: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          Stack(
            clipBehavior: Clip.none,
            children: [
              Container(
                width: 44,
                height: 44,
                decoration: BoxDecoration(
                  color: platformColor(platform),
                  borderRadius: BorderRadius.circular(12),
                ),
                alignment: Alignment.center,
                child: Text(
                  platform.glyph,
                  style: const TextStyle(
                      color: Colors.white,
                      fontSize: 17,
                      fontWeight: FontWeight.w600),
                ),
              ),
              if (ready)
                Positioned(
                  right: -3,
                  top: -3,
                  child: Container(
                    width: 10,
                    height: 10,
                    decoration: BoxDecoration(
                      color: palette.success,
                      shape: BoxShape.circle,
                      border: Border.all(color: palette.surface, width: 1.5),
                    ),
                  ),
                ),
            ],
          ),
          const SizedBox(height: 6),
          Text(
            platform.label,
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            style: TextStyle(color: palette.labelText, fontSize: 12),
          ),
        ],
      ),
    );
  }
}
