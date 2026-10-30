import 'package:flutter/material.dart';

import 'package:vdl/shared/site.dart';

/// 站点品牌色（仅 UI 用，所以放在 Flutter 层，不进 core）。
Color platformColor(SitePlatform platform) => switch (platform) {
      SitePlatform.douyin => const Color(0xFF161823),
      SitePlatform.xhs => const Color(0xFFFF2442),
      SitePlatform.bili => const Color(0xFF00A1D6),
      SitePlatform.x => const Color(0xFF0F1419),
      SitePlatform.iwara => const Color(0xFFE8453C),
      SitePlatform.pornhub => const Color(0xFF1B1B1B),
      SitePlatform.hanime1 => const Color(0xFF7B2FBE),
      SitePlatform.jm => const Color(0xFF2E7D32),
    };
