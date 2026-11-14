// Rebuild the static local icon set with the repository's existing dependencies.
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import * as icons from 'lucide-react';
import { writeFileSync } from 'node:fs';
const names = ['AudioLines','Circle','CircleCheck','Send','Minus','Square','X','Instagram','Mail','MessageSquareText','ChevronsUpDown','ScanSearch','Disc3','ContactRound','BookUser','UserRound','History','ChartNoAxesCombined','UsersRound','MessagesSquare','PanelsTopLeft','ScrollText','MailPlus','Flame','Sparkles','Settings2','PanelLeft','Search','RefreshCw','Download','Ellipsis','PanelRight','CircleUserRound','SlidersHorizontal','ArrowDown','ChevronLeft','ChevronRight','Copy','ArrowUpRight','Check','ChevronDown'];
names.push('CalendarDays','Pencil','Bell','Plus','Trash2','RotateCcw','Phone','Star');
const data = Object.fromEntries(names.map(name => [name,renderToStaticMarkup(React.createElement(icons[name],{'aria-hidden':true,focusable:false}))]));
writeFileSync(new URL('icons.js',import.meta.url),'window.ALF_ICONS = '+JSON.stringify(data)+';\n');
