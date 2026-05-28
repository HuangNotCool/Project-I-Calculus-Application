%%
%% fami-exam.cls
%%
%% Copyright (C) 2026 Hung Nguyen Duc <hung.nguyenduc@hust.edu.vn>
%%
%% This file may be distributed and/or modified under the
%% conditions of the LaTeX Project Public License (LPPL),
%% either version 1.3c of this license or (at your option)
%% any later version.
%%
%% This work has the LPPL maintenance status `maintained'.
%% The Current Maintainer of this work is Hung Nguyen Duc.
%%
%% This work consists of the file fami-exam.cls.
%%
%% This format works with texlive 2018 and beyond
\NeedsTeXFormat{LaTeX2e}
\ProvidesClass{fami-exam}[2026-01-18 FaMI midterm exam (HungND)]

\LoadClass[11pt]{article}
\RequirePackage{xparse} % Pre-2020 compat only
\RequirePackage{xstring}
\RequirePackage{enumitem}
\RequirePackage{geometry}
\RequirePackage{fancyhdr}
\RequirePackage{tgtermes}
\RequirePackage{tabularx}
\RequirePackage{setspace}
\RequirePackage{tikz}
\RequirePackage{amsthm}
\RequirePackage{multicol}
\RequirePackage{xcolor}
\RequirePackage[protrusion=false]{microtype}

% Information fields
% ==================
\gdef\Placeholder{\#\#\#\#\#\#\#\#}
\gdef\InsertMaDeThi{\Placeholder}
\gdef\MaDeThi#1{\gdef\InsertMaDeThi{#1}}
\gdef\InsertHocPhan{\Placeholder}
\gdef\HocPhan#1{\gdef\InsertHocPhan{#1}}
\gdef\InsertMaHocPhan{\Placeholder}
\gdef\MaHocPhan#1{\gdef\InsertMaHocPhan{#1}}
\gdef\InsertHocKy{\Placeholder}
\gdef\HocKy#1{\gdef\InsertHocKy{#1}}
\gdef\InsertThoiGianLam{40}
\gdef\ThoiGianLam#1{\gdef\InsertThoiGianLam{#1}}
\gdef\InsertGhiChu{\#\#\#}
\gdef\GhiChu#1{\gdef\InsertGhiChu{#1}}
\gdef\InsertGhiChuDapAn{\#\#\#}
\gdef\GhiChuDapAn#1{\gdef\InsertGhiChuDapAn{#1}}

% Metadata
% ========
\def\myclass@author{Nguyen Duc Hung <hung.nguyenduc@hust.edu.vn>}
\def\myclass@version{1.0}
\def\myclass@date{2026/01/18}
\typeout{Loaded myclass v\myclass@version\space by \myclass@author}

% Type setting & page layout
% ==========================
\setlength{\parindent}{0pt}
\onehalfspacing
\geometry{
  a4paper,
  margin={0.5in,0.25in},
  includeheadfoot,
  headsep=2ex,
  headheight=14pt,
}

\definecolor{fami-blue}{HTML}{0B6DAF}
\definecolor{hust-red}{HTML}{DE363B}
\colorlet{headings-color}{fami-blue}

% Localization
% ============

% Define new string
\NewDocumentCommand{\DefineString}{mm}{%
  \expandafter\gdef\csname @str@#1\endcsname{#2}%
}

% Use string
\NewDocumentCommand{\UseString}{m}{%
  \csname @str@#1\endcsname%
}%

% Vietnamese Strings
\def\@LoadStrings@VN{
  % -- headers and footers --
  \DefineString{hust}{Đại học Bách khoa Hà Nội}
  \DefineString{fami}{Khoa Toán - Tin}
  \DefineString{page}{Trang}
  \DefineString{question}{Câu}
  \DefineString{solution-label}{Lời giải}
  \DefineString{answers}{ĐÁP ÁN}
  % -- title --
  \DefineString{mid-term-exam}{ĐỀ KIỂM TRA GIỮA KỲ, HỌC KỲ}
  \DefineString{course-name}{Học phần}
  \DefineString{course-id}{Mã học phần}
  \DefineString{exam-duration}{Thời gian làm bài}
  \DefineString{minutes}{phút}
  % -- student info --
  \DefineString{student-id}{MSSV}
  \DefineString{class-id}{Mã lớp học}
  \DefineString{student-name}{Họ và tên sinh viên}
  \DefineString{student-ord}{STT}
  % -- sign area --
  \DefineString{total-score}{Tổng điểm}
  \DefineString{grader-sign}{Họ tên và chữ ký của cán bộ chấm thi}
  \DefineString{proctor-sign}{Họ tên và chữ ký của cán bộ coi thi}
  % -- exam information --
  \DefineString{exam-code}{Mã đề thi}
  \DefineString{exam-includes}{Đề gồm \InsertSoCauHoi{} câu}
  \DefineString{exam-remark}{Chú ý}
  \DefineString{exam-remark-text}{Không được sử dụng tài liệu}
}

% English Strings
\def\@LoadStrings@EN{
  % -- headers and footers --
  \DefineString{hust}{Hanoi University of Science and Technology}
  \DefineString{fami}{Faculty of Mathematics and Informatics}
  \DefineString{page}{Page}
  \DefineString{question}{Question}
  \DefineString{solution-label}{Solution}
  \DefineString{answers}{ANSWERS}
  % -- title --
  \DefineString{mid-term-exam}{MIDTERM EXAM, TERM}
  \DefineString{course-name}{Course}
  \DefineString{course-id}{Course ID}
  \DefineString{exam-duration}{Time duration}
  \DefineString{minutes}{minutes}
  % -- student info --
  \DefineString{student-name}{Full name}
  \DefineString{student-id}{Student's ID}
  \DefineString{class-id}{Class ID}
  \DefineString{student-ord}{Ordinal Number:}
  % -- sign area --
  \DefineString{total-score}{Total score}
  \DefineString{grader-sign}{Full name and signature of the evaluators}
  \DefineString{proctor-sign}{Full name and signature of the proctors}
  % -- exam information --
  \DefineString{exam-code}{Exam number}
  \DefineString{exam-includes}{The exam consists of \InsertSoCauHoi{} questions}
  \DefineString{exam-remark}{Remark}
}

%% Manual language selection
\NewDocumentCommand{\NgonNgu}{m}{%
  \begingroup%
  \StrSubstitute{#1}{ }{}[\NgonNgonTrim]%
  \IfStrEqCase{\NgonNgonTrim}{%
    {english}{\@LoadStrings@EN{}}%
    {anh}{\@LoadStrings@EN{}}%
    {vietnamese}{\@LoadStrings@VN{}}%
    {viet}{\@LoadStrings@VN{}}%
    {English}{\@LoadStrings@EN{}}%
    {Anh}{\@LoadStrings@EN{}}%
    {Vietnamese}{\@LoadStrings@VN{}}%
    {Viet}{\@LoadStrings@VN{}}%
  }[%
    % This is the 'default' branch, used for the error:
    \PackageError{fami-exam.cls}{Supported language: "english",
    "vietnamese", "anh", "viet"}{Supported language: "english",
    "vietnamese", "anh", "viet"}%
  ]%
  \endgroup%
}
\NgonNgu{english}

% Headers and footers
% ===================
\pagestyle{fancy}
\fancyhf{} % clear everything
\fancyhead[L]{\textit{\UseString{hust}}} % top left header
\fancyhead[R]{\textit{\UseString{fami}}} % top right header
\fancyfoot[R]{\itshape{}%
  % NOTE: \InsertSoTrang is a PITA to implement
  % Test with caution
  \UseString{page}~\thepage/\InsertSoTrang%
  ~--~\UseString{exam-code}~\InsertMaDeThi%

} % top right header
\renewcommand{\headrulewidth}{0.5pt}
\renewcommand{\footrulewidth}{0pt}

% Document structures
% ===================
\let\section\undefined
\let\subsection\undefined
\let\subsubsection\undefined
\let\paragraph\undefined

% Counter for looping and printing questions
% ------------------------------------------

\newcount\total@choices
\newcount\total@sections
\newcount\total@questions
\newcount\total@questions@persect
\newcount\sect@idx
\newcount\choice@idx
\newcount\question@idx

\sect@idx=0
\total@sections=0
\total@questions=0

\newcounter{question}

% Define new section command
% --------------------------
% Section are dynamically stored in this format
% \sect@<sect@idx>@title
% \sect@<sect@idx>@nquestions
% \sect@<sect@idx>@at-question: print the section title at this question index
\def\thesection{\Roman{section}}
\NewDocumentEnvironment{section}{m+b}{%
  % --- Counters ---
  \refstepcounter{section}%
  \global\advance\total@sections by 1%
  % --- Store start positions ---
  \edef\etotalquesions{\number\total@questions}
  \global\expandafter\edef\csname
  sect@\number\total@sections @at-question
  \endcsname{\etotalquesions}
  % --- Store section title
  \expandafter\gdef\csname %
  sect@\number\total@sections @title
  \endcsname{#1}%
  % --- Run all the question definitions
  #2 % the definition is in the body
}{}

% Question and solution typesetting
% ---------------------------------
\newtheoremstyle{exam-question}% name
{\topsep} % space above
{\topsep} % space below
{} % body font
{} % indent amount
{\color{headings-color}\bfseries} % theorem head font
{.} % Punctuation after theorem head
{0.5em} % Space after theorem head
{} % Theorem head spec (can be left empty, meaning `normal')
\theoremstyle{exam-question}
\newtheorem{question-typeset}{\UseString{question}}

\newtheoremstyle{exam-solution}% name
{0.5\topsep} % space above
{\topsep} % space below
{} % body font
{} % indent amount
{\color{black!75}\bfseries} % theorem head font
{.} % Punctuation after theorem head
{0.5em} % Space after theorem head
{} % Theorem head spec (can be left empty, meaning `normal')
\theoremstyle{exam-solution}
\newtheorem*{solution-typeset}{\UseString{solution-label}}

% Questions
% ---------
% dynamically define macros related to questions:
% - \question@<question>: the question body
% - \question@<question><choice>: the choice body
% - \question@correct@<question><choice>: wherether the choice is correct
% - \question@column@<question>: number of columns for the choices
% - \question@nchoice@<question>: number of choices of the question
\NewDocumentEnvironment{question}{+b}{%
  \begingroup % scope
  %
  % --- increase necessary counters ---
  %
  \stepcounter{question}% increase question counter
  \global\advance\total@questions@persect by 1
  \global\advance\total@questions by 1
  %
  % --- Default number of columns for choice is 2 ---
  %
  \expandafter\gdef%
  \csname question@column@\arabic{question}%
  \endcsname{2}%
  %
  % --- define the question body ---
  %
  \expandafter\gdef% define...
  \csname question@\arabic{question}% \question@1...
  \endcsname{#1}% as the body
  %
  % --- commit number of choices ---
  %
  \expandafter\gdef% define...
  \csname question@nchoices\arabic{question}% \question@1...
  \endcsname{\arabic{numQuestionChoice}}
  %
  % --- exit scope ---
  %
  \endgroup
}{}

% Store solution to print them later
% ----------------------------------
\NewDocumentEnvironment{solution}{+b}{%
  \begingroup{}%
  \expandafter\gdef% define question@solution@###
  \csname question@solution@\number\question@idx% \question@1...
  \endcsname{%
    \begin{solution-typeset}#1%
    \end{solution-typeset}%
  }%
  \endgroup{}%
}{}

%% Should we show solution sheet?
%--------------------------------
% This is not end-user API, so no need to be friendly
\newif\if@FamiExam@IfShowAnswers@
\let\ShowAnswers\@FamiExam@IfShowAnswers@true
\let\HideAnswers\@FamiExam@IfShowAnswers@false
\ShowAnswers{}

% Allow hide or show detailed solutions
% -------------------------------------
\newif\if@FamiExam@IfShowSolution@
\NewDocumentCommand{\LoiGiai}{m}{%
  \begingroup{}%
  \StrSubstitute{#1}{ }{}[\TrimmedFlag]
  \IfStrEqCase{\TrimmedFlag}{%
    {show}{\global\@FamiExam@IfShowSolution@true}%
    {hide}{\global\@FamiExam@IfShowSolution@false}%
    {true}{\global\@FamiExam@IfShowSolution@true}%
    {false}{\global\@FamiExam@IfShowSolution@false}%
    {hien}{\global\@FamiExam@IfShowSolution@true}%
    {an}{\global\@FamiExam@IfShowSolution@false}%
  }[%
    % This is the 'default' branch, used for the error:
    \PackageError{fami-exam.cls}{Unsupported option: show | hide | an
    | hien}{Unsupported option: show | hide | an | hien}%
  ]%
  \endgroup%
}
\@FamiExam@IfShowSolution@true

%% Loop over questions and print all of them
%% The starred version prints solutions.
\newif\if@FamiExam@PrintingSolutions@
\NewDocumentCommand{\GenericPrintQuestions}{s}{
  \begingroup
  %
  % --- Format answers as answers or as choice
  %
  \IfBooleanTF{#1}{%
    \@FamiExam@PrintingSolutions@true%
    \let\eFormatCorrect\FormatCorrect%
  }{\let\eFormatCorrect\FormatIncorrect%
    \@FamiExam@PrintingSolutions@false%
  }
  %
  %
  % --- Set command to current column inside the question body ---
  %
  \newcommand{\DatCot}[1][2]{%
    \expandafter\gdef%
    \csname question@column@\number\question@idx%
    \endcsname{##1}%
  }
  %
  % --- Show solutions ---
  %
  \newcommand{\PrintDetailedSolution}{%
    % If is showing solution
    \if@FamiExam@PrintingSolutions@{}%
    % If allow detailed solution to be shown
    \if@FamiExam@IfShowSolution@{}
    \csname question@solution@\number\question@idx \endcsname
    \fi\fi%
  }
  %
  % --- Choice commands ---
  %
  \newcommand{\Choice}[2]{%
    % add total number of choices for the question
    \advance\total@choices by 1
    \advance\choice@idx by 1
    % save the choice in a macro
    \expandafter\gdef\csname
    question@\number\question@idx @choice@ \number\choice@idx
    \endcsname{##2}%
    % save wherether the choice is correct or not
    % 1 means correct, 2 means not
    \expandafter\gdef\csname
    question@correct@\number\question@idx\number\choice@idx
    \endcsname{##1}%
  }
  \newcommand{\Dung}[1]{\Choice{1}{##1}}
  \newcommand{\Sai}[1]{\Choice{0}{##1}}
  %
  % --- helpful commands that expand depends on the counter ---
  %
  \def\CurrentChoiceBody{\csname%
    question@\number\question@idx @choice@ \number\choice@idx
  \endcsname}
  \def\CurrentChoiceCorrect{\csname%
    question@correct@\number\question@idx\number\choice@idx
  \endcsname}
  \def\CurrentNumCols{\csname%
    question@column@\number\question@idx%
  \endcsname}
  \def\CurrentSectionTitle{\csname
    sect@\number\sect@idx @title
  \endcsname}
  \def\CurrentSectionPosition{\csname
    sect@\number\sect@idx @at-question
  \endcsname}
  %
  % --- Loop over questions
  %
  \setcounter{section}{1}
  \global\sect@idx=1
  \question@idx=0 % Question counter
  \setcounter{question-typeset}{0} % Typeset question counter
  \loop
  \ifnum\sect@idx>\total@sections\else
  \begingroup
  \ifnum\CurrentSectionPosition=\question@idx
  \edef\sectiontitle{\Roman{section}.~\CurrentSectionTitle}
  \global\advance\sect@idx\@ne
  \stepcounter{section}
  \textbf{\large\sectiontitle}\par
  \fi
  \endgroup
  \fi
  \advance\question@idx by 1
  {
    \choice@idx=0\vbox{
      \begin{question-typeset}
        % --- Show question body ---
        \DatCot[4] % Default 4 columns
        \csname%
        question@\number\question@idx
        \endcsname%
        % --- Show choices ---
        \ifnum\total@choices>0%
        \par\vspace{0.35em}%
        \choice@idx=1%
        \setlength{\multicolsep}{1.5pt}
        \begin{multicols}{\CurrentNumCols}
          \begin{enumerate}[nosep, itemsep=2pt]
              \loop {
                \ifnum\CurrentChoiceCorrect=1
                \eFormatCorrect{\CurrentChoiceBody}
                \else
                \FormatIncorrect{\CurrentChoiceBody}
                \fi
              }
              \advance\choice@idx by 1
              \ifnum\choice@idx>\total@choices\else
              \repeat
          \end{enumerate}
        \end{multicols}%
        \PrintDetailedSolution{}%
        \fi
    \end{question-typeset}}
  }
  \ifnum\question@idx<\value{question}
  \repeat
  \endgroup
}

\NewDocumentCommand{\PrintQuestions}{}{\GenericPrintQuestions{}
\WriteTotalQuestions{}}
\NewDocumentCommand{\PrintAnswers}{}{\GenericPrintQuestions*{}}

% Write total number of question to the output
\providecommand{\InsertSoCauHoi}{\#\#\#}
\newcommand{\WriteTotalQuestions}{%
  \edef\e@total@questions{\the\total@questions}
  \protected@write\@auxout{}{%
    \string\gdef\string\InsertSoCauHoi{\e@total@questions}%
  }
}

%% Write total number of pages to the output
\providecommand{\InsertSoTrang}{\#\#\#}
\newcommand{\WriteTotalPages}{%
  \edef\e@total@page{\thepage}
  \protected@write\@auxout{}{%
    \string\gdef\string\InsertSoTrang{\e@total@page}%
  }
}

\AtEndDocument{\ignorespaces
  \MakeExamTitle{}%
  \PrintQuestions{}%
  \if@FamiExam@IfShowAnswers@
  \clearpage{}%
  \setcounter{page}{1}%
  \MakeAnswerTitle{}%
  \PrintAnswers{}%
  \fi
  \WriteTotalPages{}
\ignorespacesafterend}

% Circled text
% ============
\tikzstyle{Choice} = [
  color = headings-color,
  shape = circle,
  inner sep=2pt
]
\newcommand{\Circled}[1]{%
  \tikz[baseline=(char.base)]{%
    \node[Choice,
      draw=hust-red,
    color=hust-red] (char) {\textbf{#1}};%
  }
}
\newcommand{\NonCircled}[1]{%
  \tikz[baseline=(char.base)]{%
    \node[Choice] (char) {\textbf{#1\rlap{.}}};%
  }
}

% Format correct/incorrect answer
% ===============================
\newcommand{\FormatCorrect}[1]{%
\item[\Circled{\refstepcounter{enumi}\Alph{enumi}}] #1%
}
\newcommand{\FormatIncorrect}[1]{%
\item[\NonCircled{\refstepcounter{enumi}\Alph{enumi}}] #1%
}

% Patching \maketitle command
% ===========================
\def\ddotfill{%
  \leavevmode%
  \cleaders \hb@xt@ .44em{\hss.\hss}\hfill%
  \kern\z@%
  \cleaders \hb@xt@ .44em{\hss.\hss}\hfill%
\kern\z@}
\let\maketitle\undefined

\newcommand{\blap}[1]{\vbox to 0pt{\hbox{#1}\vss}}
\newcommand{\tlap}[1]{\vbox to 0pt{\vss\hbox{#1}}}
\NewDocumentCommand{\MakeAnswerTitle}{}{\ignorespaces
  \begingroup
  {\rlap{\fbox{\textbf{\UseString{exam-code}:~\InsertMaDeThi}}}}%
  \par\nointerlineskip%
  {\centering\textbf{\LARGE{}{\UseString{answers}}}\par}%
  \endgroup%
  \par\medskip%
  \textbf{\color{hust-red}\InsertGhiChuDapAn}%%
  \par\bigskip%
\ignorespacesafterend}

% Make the exam title
% -------------------
\newcolumntype{C}{>{\centering\arraybackslash}X}
\NewDocumentCommand{\MakeExamTitle}{}{\ignorespaces
  \begin{tabularx}{\textwidth}{|c|X|}
    % Give some spaces for the student to write name
    \hline & \\[-2.25ex]
    \textbf{\UseString{mid-term-exam}~\InsertHocKy{}} &
    \textbf{\UseString{student-name}:~}\dotfill
    \\
    \UseString{course-name}:~\InsertHocPhan{}.
    \UseString{course-id}:~\InsertMaHocPhan{} &
    \UseString{student-id}:~\ddotfill~%
    \UseString{student-ord}:~\dotfill
    \\
    \textit{\UseString{exam-duration}:~\InsertThoiGianLam{}~\UseString{minutes}}
    &
    \UseString{class-id}:~\dotfill
    \\[1ex]
    \hline
  \end{tabularx}%
  \par\nointerlineskip
  \begin{tabularx}{\textwidth}{|p{3.5cm}|C|C|}
    \centering\UseString{total-score}&
    \UseString{grader-sign}&
    \UseString{proctor-sign}\\[2cm]
    \hline
  \end{tabularx}%
  \par\smallskip
  {\centering%
    \fbox{\textbf{\UseString{exam-code}:~\InsertMaDeThi}}%
    \rlap{~(\UseString{exam-includes})}%
  \par}
  \smallskip
  \par
  \textbf{\UseString{exam-remark}}: \emph{\InsertGhiChu}
  \par\medskip
\ignorespacesafterend}

% User-facing \maketitle command
% ------------------------------
\NewDocumentCommand{\maketitle}{s}{\ignorespaces
  \IfBooleanTF{#1}{\MakeAnswerTitle}{\MakeExamTitle}
\ignorespacesafterend}
