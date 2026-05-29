// Interface + enum: exercises the SymbolInformation Kind mapping and the
// `implements` relationship resolved on the consuming class (see circle.ts).
export interface Shape {
  area(): number;
}

export enum Kind {
  Round,
  Square,
}
