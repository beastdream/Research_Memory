import sys
from collect_arxiv import main

if __name__ == '__main__':
    main(['normalize', *sys.argv[1:]])
